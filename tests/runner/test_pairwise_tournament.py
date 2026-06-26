from __future__ import annotations

import json
from collections.abc import Iterator

import pytest

from selfevals.runner.adapters import AdapterRequest, AdapterResponse, EmbeddedAdapter
from selfevals.runner.launch import ensure_workspace_by_id
from selfevals.runner.pairwise_tournament import Candidate, run_tournament
from selfevals.schemas.pairwise_verdict import PairwiseVerdict
from selfevals.schemas.tournament import Tournament
from selfevals.storage.interface import ListFilter, StorageInterface, WorkspaceScope

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


@pytest.fixture
def scope(storage: StorageInterface) -> Iterator[WorkspaceScope]:
    """A workspace scope over a fresh per-test Postgres database (workspace seeded
    first — tournament/verdict rows carry a real FK to ``workspaces``)."""
    ensure_workspace_by_id(storage, WS)
    with storage.open(WS) as s:
        yield s


def _ranked_judge() -> EmbeddedAdapter:
    """A judge with a fixed quality order a > b > c: it reads the two responses
    out of the prompt and prefers whichever ranks higher."""
    order = {"out-a": 3, "out-b": 2, "out-c": 1}

    def fn(req: AdapterRequest) -> AdapterResponse:
        prompt = req.input["messages"][0]["content"]
        # Only the two tokens actually present in this prompt matter.
        present = [t for t in order if t in prompt]
        present.sort(key=lambda t: prompt.find(t))
        first, second = present[0], present[1]
        # The text that appears first in the prompt is "Response A".
        preferred = "a" if order[first] > order[second] else "b"
        return AdapterResponse(
            content=json.dumps({"preferred": preferred, "margin": 0.5, "reason": "quality"})
        )

    return EmbeddedAdapter(fn)


def _candidates() -> list[Candidate]:
    return [
        Candidate(id="a", output_text="out-a"),
        Candidate(id="b", output_text="out-b"),
        Candidate(id="c", output_text="out-c"),
    ]


@pytest.mark.asyncio
async def test_all_pairs_elo_produces_expected_order(scope: WorkspaceScope) -> None:
    result = await run_tournament(
        scope,
        candidates=_candidates(),
        judge_adapter=_ranked_judge(),
        rubric="which is better?",
        strategy="all_pairs",
        method="elo",
    )
    ids = [r.candidate_id for r in result.ranking.rows]
    assert ids == ["a", "b", "c"]
    assert result.n_comparisons == 3  # n(n-1)/2


@pytest.mark.asyncio
async def test_persists_verdict_per_comparison_and_tournament(scope: WorkspaceScope) -> None:
    result = await run_tournament(
        scope,
        candidates=_candidates(),
        judge_adapter=_ranked_judge(),
        rubric="r",
        strategy="all_pairs",
        method="bradley_terry",
        experiment_id="exp_1",
    )
    verdicts = scope.list_entities(PairwiseVerdict, ListFilter())
    assert len(verdicts) == 3
    tournaments = scope.list_entities(Tournament, ListFilter())
    assert len(tournaments) == 1
    assert tournaments[0].id == result.tournament.id
    assert result.tournament.method == "bradley_terry"
    assert result.ranking.rows[0].candidate_id == "a"


@pytest.mark.asyncio
async def test_vs_baseline_is_linear(scope: WorkspaceScope) -> None:
    result = await run_tournament(
        scope,
        candidates=_candidates(),
        judge_adapter=_ranked_judge(),
        rubric="r",
        strategy="vs_baseline",
        baseline_id="c",
        method="elo",
    )
    assert result.n_comparisons == 2  # a vs c, b vs c


@pytest.mark.asyncio
async def test_swiss_runs_rounds(scope: WorkspaceScope) -> None:
    cands = [Candidate(id=f"c{i}", output_text=f"out-{i}") for i in range(4)]

    def fn(req: AdapterRequest) -> AdapterResponse:
        return AdapterResponse(
            content=json.dumps({"preferred": "a", "margin": 0.4, "reason": "x"})
        )

    result = await run_tournament(
        scope,
        candidates=cands,
        judge_adapter=EmbeddedAdapter(fn),
        rubric="r",
        strategy="swiss",
        swiss_rounds=2,
        method="elo",
    )
    # 4 candidates, 2 rounds → ~2 pairs/round → up to 4 comparisons.
    assert 0 < result.n_comparisons <= 4


@pytest.mark.asyncio
async def test_swap_and_average_doubles_judge_calls(scope: WorkspaceScope) -> None:
    calls = {"n": 0}

    def fn(req: AdapterRequest) -> AdapterResponse:
        calls["n"] += 1
        return AdapterResponse(
            content=json.dumps({"preferred": "a", "margin": 0.5, "reason": "x"})
        )

    await run_tournament(
        scope,
        candidates=[Candidate(id="a", output_text="oa"), Candidate(id="b", output_text="ob")],
        judge_adapter=EmbeddedAdapter(fn),
        rubric="r",
        strategy="all_pairs",
        swap_and_average=True,
        method="elo",
    )
    # 1 pair, judged twice (A/B and B/A).
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_flaky_judge_comparison_is_dropped(scope: WorkspaceScope) -> None:
    def fn(req: AdapterRequest) -> AdapterResponse:
        raise RuntimeError("rate limited")

    result = await run_tournament(
        scope,
        candidates=_candidates(),
        judge_adapter=EmbeddedAdapter(fn),
        rubric="r",
        strategy="all_pairs",
        method="elo",
    )
    # All comparisons dropped → no verdicts, but the tournament still completes.
    assert result.n_comparisons == 0
    assert scope.list_entities(PairwiseVerdict, ListFilter()) == []
    assert len(scope.list_entities(Tournament, ListFilter())) == 1
