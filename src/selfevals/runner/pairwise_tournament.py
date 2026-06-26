"""Pairwise tournament orchestrator: rank N candidates via an LLM judge.

Glues the three pure pieces together and persists the result:

    pairing (which pairs to compare)  →  judge_pair (who wins each)  →
    ranking (Elo / Bradley-Terry)     →  PairwiseVerdict + Tournament (storage)

This is the second module (with `pairwise_ops.py`) that touches storage for the
pairwise feature — the migration cut-point. It does NOT touch the optimization
loop: candidates are outputs already in hand (persisted traces or snapshots),
not live runs. Async-first; comparisons fan out concurrently, bounded by a
semaphore, the same pattern as `optimization/loop.py`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from selfevals.graders.pairwise import PairwiseRubric, judge_pair
from selfevals.optimization import pairing
from selfevals.optimization.ranking import (
    Outcome,
    Ranking,
    RankingMethod,
    bradley_terry,
    elo_ratings,
)
from selfevals.runner.pairwise_ops import ingest_verdicts
from selfevals.schemas.pairwise_verdict import PairRef, PairwiseVerdict
from selfevals.schemas.tournament import Tournament, TournamentRow

if TYPE_CHECKING:
    from selfevals.runner.adapters import AgentAdapter
    from selfevals.storage.interface import WorkspaceScope

Pair = tuple[str, str]
DEFAULT_CONCURRENCY = 8


@dataclass(frozen=True)
class Candidate:
    """One entrant: a stable id plus the text to be judged.

    `output_text` is what the judge reads. `trace_id` (optional) links the
    verdict back to the persisted trace; `case_input` is shown to the judge as
    the shared task context (all candidates in a tournament answer the same
    task)."""

    id: str
    output_text: str
    trace_id: str | None = None


@dataclass(frozen=True)
class TournamentResult:
    tournament: Tournament
    ranking: Ranking
    n_comparisons: int


def _plan_pairs(
    strategy: str,
    candidate_ids: list[str],
    *,
    baseline_id: str | None,
    comparisons_per_candidate: int,
) -> list[Pair]:
    if strategy == "vs_baseline":
        if baseline_id is None:
            raise ValueError("strategy 'vs_baseline' requires baseline_id")
        return pairing.vs_baseline(candidate_ids, baseline_id)
    if strategy == "all_pairs":
        return pairing.all_pairs(candidate_ids)
    if strategy == "sampled":
        return pairing.sampled(
            candidate_ids, comparisons_per_candidate=comparisons_per_candidate
        )
    raise ValueError(f"unknown or non-planned strategy: {strategy!r}")


async def run_tournament(
    scope: WorkspaceScope,
    *,
    candidates: list[Candidate],
    judge_adapter: AgentAdapter,
    rubric: str,
    case_input: Any = None,
    strategy: str = "all_pairs",
    method: RankingMethod = "elo",
    baseline_id: str | None = None,
    comparisons_per_candidate: int = 3,
    swiss_rounds: int = 3,
    swap_and_average: bool = False,
    experiment_id: str | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> TournamentResult:
    """Run a tournament and persist its verdicts + ranking.

    `strategy` is one of `vs_baseline` / `all_pairs` / `sampled` / `swiss`.
    `method` is `elo` or `bradley_terry`. Returns the persisted `Tournament`
    plus the in-memory `Ranking`. Pairs whose judge call errors are skipped (a
    dropped comparison, not a failed tournament) so one flaky judge call doesn't
    sink the run.
    """
    by_id = {c.id: c for c in candidates}
    ids = sorted(by_id)
    template = PairwiseRubric(rubric=rubric)
    sem = asyncio.Semaphore(concurrency)

    async def _compare(a_id: str, b_id: str) -> tuple[Outcome, PairwiseVerdict] | None:
        a, b = by_id[a_id], by_id[b_id]
        async with sem:
            try:
                decision = await judge_pair(
                    judge_adapter,
                    template,
                    case_input=case_input,
                    response_a=a.output_text,
                    response_b=b.output_text,
                    workspace_id=scope.workspace_id,
                    case_id=a.id,  # the "case" context is the compared pair
                    grader_name="tournament",
                    swap_and_average=swap_and_average,
                )
            except Exception:
                return None  # drop a flaky comparison, keep the tournament
        verdict = _verdict_from(
            decision, a, b, scope.workspace_id, experiment_id, swap_and_average
        )
        outcome = _outcome_from(decision, a_id, b_id)
        return outcome, verdict

    # Plan + run. Swiss is adaptive: it needs standings between rounds, so it
    # runs round-by-round rather than from a single up-front plan.
    if strategy == "swiss":
        results = await _run_swiss(
            ids, _compare, rounds=swiss_rounds
        )
    else:
        pairs = _plan_pairs(
            strategy,
            ids,
            baseline_id=baseline_id,
            comparisons_per_candidate=comparisons_per_candidate,
        )
        gathered = await asyncio.gather(*(_compare(a, b) for a, b in pairs))
        results = [r for r in gathered if r is not None]

    outcomes = [o for o, _ in results]
    verdicts = [v for _, v in results]
    if verdicts:
        ingest_verdicts(scope, verdicts, validate_refs=False)

    ranking = (elo_ratings if method == "elo" else bradley_terry)(ids, outcomes)
    tournament = _persist_tournament(
        scope,
        ranking=ranking,
        candidate_ids=ids,
        strategy=strategy,
        method=method,
        baseline_id=baseline_id,
        n_comparisons=len(outcomes),
        swap_and_average=swap_and_average,
        experiment_id=experiment_id,
    )
    return TournamentResult(
        tournament=tournament, ranking=ranking, n_comparisons=len(outcomes)
    )


async def _run_swiss(
    ids: list[str],
    compare: Any,
    *,
    rounds: int,
) -> list[tuple[Outcome, PairwiseVerdict]]:
    """Run `rounds` Swiss rounds, updating standings (running wins) between them."""
    standings = dict.fromkeys(ids, 0.0)
    played: set[Pair] = set()
    results: list[tuple[Outcome, PairwiseVerdict]] = []
    for _ in range(rounds):
        round_pairs = pairing.next_swiss_round(ids, standings, played)
        if not round_pairs:
            break
        for a, b in round_pairs:
            key = (a, b) if a < b else (b, a)
            played.add(key)
        gathered = await asyncio.gather(*(compare(a, b) for a, b in round_pairs))
        for res in gathered:
            if res is None:
                continue
            outcome, _ = res
            results.append(res)
            if not outcome.tie:
                standings[outcome.winner_id] += 1.0
    return results


def _outcome_from(decision: Any, a_id: str, b_id: str) -> Outcome:
    if decision.preferred == "a":
        return Outcome(winner_id=a_id, loser_id=b_id, weight=1.0 + decision.margin)
    if decision.preferred == "b":
        return Outcome(winner_id=b_id, loser_id=a_id, weight=1.0 + decision.margin)
    return Outcome(winner_id=a_id, loser_id=b_id, tie=True)


def _verdict_from(
    decision: Any,
    a: Candidate,
    b: Candidate,
    workspace_id: str,
    experiment_id: str | None,
    swap_and_average: bool,
) -> PairwiseVerdict:
    return PairwiseVerdict(
        id=PairwiseVerdict.make_id(),
        workspace_id=workspace_id,
        a_ref=PairRef(kind="arbitrary", trace_id=a.trace_id, content_snapshot=a.output_text),
        b_ref=PairRef(kind="arbitrary", trace_id=b.trace_id, content_snapshot=b.output_text),
        preferred=decision.preferred,
        margin=decision.margin,
        rationale=decision.reason,
        judge_kind="llm",
        judge_id="llm:tournament",
        experiment_id=experiment_id,
        position="ab" if not swap_and_average else None,
    )


def _persist_tournament(
    scope: WorkspaceScope,
    *,
    ranking: Ranking,
    candidate_ids: list[str],
    strategy: str,
    method: RankingMethod,
    baseline_id: str | None,
    n_comparisons: int,
    swap_and_average: bool,
    experiment_id: str | None,
) -> Tournament:
    rows = [
        TournamentRow(
            candidate_id=r.candidate_id,
            rank=i + 1,
            score=r.score,
            wins=r.wins,
            losses=r.losses,
            ties=r.ties,
            n_comparisons=r.n_comparisons,
        )
        for i, r in enumerate(ranking.rows)
    ]
    tournament = Tournament(
        id=Tournament.make_id(),
        workspace_id=scope.workspace_id,
        experiment_id=experiment_id,
        strategy=strategy,  # type: ignore[arg-type]
        method=method,
        candidate_ids=candidate_ids,
        baseline_id=baseline_id,
        n_comparisons=n_comparisons,
        swap_and_average=swap_and_average,
        ranking=rows,
    )
    scope.put_entity(tournament)
    return tournament
