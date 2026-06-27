"""`selfevals demo` — one command that seeds a fully real, end-to-end workspace.

The dogfood harness. It composes the pieces that already exist — `seed_workspace`,
the `run` path (`build_loop` + `loop.run()`), `run_pairwise_tournament`,
`ingest_pairwise_verdicts` — into a single reproducible flow so that, after one
command, `selfevals serve` shows every web view backed by **real** data:

  1. seed the `demo` workspace (+ canonical failure taxonomy),
  2. run two real experiments through the Anthropic-backed example agent
     (`examples/hello_llm` single-shot + `examples/hello_chat` multi-turn, the
     latter populating the thread viewer with per-turn traces),
  3. run a real pairwise tournament whose candidates are the agent's own
     replies and whose judge is a real LLM call,
  4. ingest a couple of "human" verdicts on the same pairs so the LLM↔human
     calibration shows a real, < 100% agreement rate.

"Real" means real LLM calls: the example agent calls Anthropic whenever
`ANTHROPIC_API_KEY` is set, and falls back to a deterministic fake otherwise (so
the command still works — and stays smoke-testable — without credentials). The
`run.sandbox` mode is unrelated to this: `mock` is a routing mode, not a fake LLM.

This module is deliberately thin orchestration: no new eval logic lives here.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from selfevals.api import pairwise_ops as api_pairwise
from selfevals.api import queries as api_queries
from selfevals.api.schemas import (
    IngestPairwiseVerdict,
    PairRefBody,
    RunTournamentRequest,
    TournamentCandidateBody,
)
from selfevals.cli import _friendly
from selfevals.cli.commands import CommandError, _ensure_cwd_on_path, _storage
from selfevals.runner.launch import build_loop, ensure_workspace, payload_router_for_db
from selfevals.storage.factory import resolve_storage_url
from selfevals.storage.interface import StorageInterface
from selfevals.storage.seed import seed_failure_taxonomy, seed_workspace

# The pairwise judge for the tournament. NOTE: this is the *pairwise* judge
# (returns {preferred, margin, reason}), not the rubric judge (`...:judge`,
# which scores a single output) — the tournament needs the comparative shape.
# Real LLM call when ANTHROPIC_API_KEY is set (same fall-back contract).
_JUDGE_ENTRYPOINT = "examples.hello_llm.agent:judge_pairwise"
_TOURNAMENT_RUBRIC = (
    "You are ranking customer-support replies. Prefer the reply that is more "
    "empathetic, more concrete, and more clearly offers an actionable next step. "
    "Reply with the better candidate."
)

# The two example specs the harness runs, relative to the repo root (cwd).
_DEMO_SPECS = (
    "examples/hello_llm/experiment.yaml",
    "examples/hello_chat/experiment.yaml",
)


def cmd_demo(args: argparse.Namespace) -> int:
    """Seed a real, end-to-end demo workspace. Idempotent on the `demo` slug.

    With `--fresh`, the existing `demo` workspace's experiments are left in place
    and new ones are appended (the seed itself is idempotent); `--fresh` instead
    signals intent to start from a clean db, which the user does by pointing
    `--db` at a fresh file. We keep the flag for discoverability and print the
    guidance rather than deleting data silently.
    """
    _ensure_cwd_on_path()
    storage = _storage(args)
    try:
        seeded = seed_workspace(
            storage,
            slug=args.slug,
            name=args.name or args.slug,
            user_id=args.user,
        )
        ws_id = seeded.workspace.id
        modes = seed_failure_taxonomy(storage, workspace_id=ws_id)
        print(f"workspace id={ws_id} slug={seeded.workspace.slug}")
        print(f"failure-mode taxonomy: {len(modes)} canonical mode(s)")

        experiment_ids: list[str] = []
        for spec_path in _DEMO_SPECS:
            if not Path(spec_path).exists():
                print(f"  skip {spec_path} (not found — run from the repo root)")
                continue
            exp_id = _run_demo_experiment(storage, args, spec_path, ws_id)
            if exp_id is not None:
                experiment_ids.append(exp_id)
                print(f"  ran {spec_path} → experiment {exp_id}")

        if not experiment_ids:
            raise CommandError(
                "no demo experiment ran — make sure you invoke `selfevals demo` "
                "from the repo root so examples/ is importable."
            )

        # Pairwise on whichever experiment yielded the most comparable replies
        # (the conversational one tends to win — every turn has text). Best-effort:
        # a tournament failure must not sink the demo.
        pairwise_exp = _experiment_with_most_candidates(storage, ws_id, experiment_ids)
        _seed_pairwise(
            storage, ws_id=ws_id, experiment_id=pairwise_exp, human_judge_id=args.user
        )
    finally:
        storage.close()

    db_label = resolve_storage_url(args.db)
    print()
    print("demo ready. Explore it with:")
    print(f"  uv run selfevals serve --db {args.db or db_label}")
    print(f"  → open the web UI and pick the `{args.slug}` workspace")
    return 0


def _run_demo_experiment(
    storage: StorageInterface,
    args: argparse.Namespace,
    spec_path: str,
    ws_id: str,
) -> str | None:
    """Load a spec, point it at the demo workspace, run it persisting traces.

    Mirrors `cmd_run`'s persisted path (build_loop + loop.run + payload router)
    but always persists with `persist_traces=all` so the trace viewer has full
    prompt/response payloads to show. Returns the experiment id, or None on a
    handled load error.
    """
    import asyncio

    try:
        spec = _friendly.load_spec(spec_path, workspace_id=ws_id)
    except Exception as exc:  # surface as a skip, not a crash
        print(f"  skip {spec_path}: {exc}")
        return None

    spec.experiment.run.persist_traces = "all"

    ensure_workspace(storage, spec)
    scope = storage.open(spec.workspace_id)
    try:
        payload_router = payload_router_for_db(
            resolve_storage_url(args.db), spec.workspace_id
        )
        loop = build_loop(spec, scope=scope, repetitions_per_case=1, payload_router=payload_router)
        result = asyncio.run(loop.run())
        from selfevals.runner.baseline import maybe_autoset_baseline

        maybe_autoset_baseline(scope, spec, result)
    finally:
        scope.close()
    return result.experiment.id


def _candidates_for(
    storage: StorageInterface, ws_id: str, experiment_id: str
) -> list[TournamentCandidateBody]:
    """The agent's own replies on the best iteration, as tournament candidates.

    Read via the same `experiment_results` query the web uses — only rows whose
    `detected.content` is non-empty become candidates (a structured-only reply
    has no comparable text)."""
    results = api_queries.experiment_results(
        storage, workspace_id=ws_id, experiment_id=experiment_id
    )
    candidates: list[TournamentCandidateBody] = []
    if results is not None:
        for row in results.cases:
            text = row.detected.content if row.detected else None
            if text:
                candidates.append(
                    TournamentCandidateBody(
                        id=row.case_id, output_text=text, trace_id=row.trace_id
                    )
                )
    return candidates


def _experiment_with_most_candidates(
    storage: StorageInterface, ws_id: str, experiment_ids: list[str]
) -> str:
    """Pick the experiment with the most comparable replies (ties → first)."""
    return max(
        experiment_ids,
        key=lambda exp: len(_candidates_for(storage, ws_id, exp)),
    )


def _seed_pairwise(
    storage: StorageInterface, *, ws_id: str, experiment_id: str, human_judge_id: str
) -> None:
    """Run a real tournament + ingest human verdicts for the experiment.

    Candidates are the agent's own replies on the best iteration. We need at
    least two with text to compare; if fewer, we skip pairwise (the rest of the
    demo is still valid).
    """
    candidates = _candidates_for(storage, ws_id, experiment_id)

    if len(candidates) < 2:
        print("  pairwise: skipped (need ≥2 candidate replies with content)")
        return

    # Cap the field so the demo tournament stays fast and cheap.
    candidates = candidates[:4]
    try:
        tournament = api_pairwise.run_pairwise_tournament(
            storage,
            workspace_id=ws_id,
            experiment_id=experiment_id,
            request=RunTournamentRequest(
                candidates=candidates,
                judge_entrypoint=_JUDGE_ENTRYPOINT,
                rubric=_TOURNAMENT_RUBRIC,
                strategy="all_pairs",
                method="elo",
                comparisons_per_candidate=2,
            ),
        )
        print(
            f"  pairwise: tournament {tournament.id} "
            f"({tournament.n_comparisons} comparisons, {len(candidates)} candidates)"
        )
    except api_pairwise.PairwiseApiError as exc:
        print(f"  pairwise: tournament skipped ({exc})")
        return

    # Ingest a couple of "human" verdicts on the first two candidates, with one
    # deliberate disagreement vs the LLM so calibration shows agreement < 100%.
    a, b = candidates[0], candidates[1]
    verdicts = [
        IngestPairwiseVerdict(
            a_ref=PairRefBody(kind="agent_output", trace_id=a.trace_id, case_id=a.id),
            b_ref=PairRefBody(kind="agent_output", trace_id=b.trace_id, case_id=b.id),
            preferred="a",
            margin=0.6,
            rationale="Human: A acknowledges the issue more directly.",
            judge_kind="human",
            judge_id=human_judge_id,
            case_id=a.id,
        ),
        IngestPairwiseVerdict(
            a_ref=PairRefBody(kind="agent_output", trace_id=a.trace_id, case_id=a.id),
            b_ref=PairRefBody(kind="agent_output", trace_id=b.trace_id, case_id=b.id),
            preferred="b",
            margin=0.3,
            rationale="Human (second rater): B's next step is more concrete.",
            judge_kind="human",
            judge_id="reviewer-2",
            case_id=a.id,
        ),
    ]
    try:
        summary = api_pairwise.ingest_pairwise_verdicts(
            storage, workspace_id=ws_id, experiment_id=experiment_id, verdicts=verdicts
        )
        print(f"  pairwise: ingested {summary.ingested} human verdict(s)")
    except api_pairwise.PairwiseApiError as exc:
        print(f"  pairwise: verdict ingest skipped ({exc})")
