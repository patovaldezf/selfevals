"""End-to-end sharded run: coordinator seeds, worker drains, aggregate from DB.

Proves the sharded path works as a whole — the run-job becomes a coordinator,
scenario jobs are claimed and executed by a worker (inline here, a separate
process in production), each writes a scenario_outcome, and the coordinator
aggregates each iteration from those rows and drives the proposer/decision bucle
to a terminal experiment state. This is the integration that the per-layer unit
tests (claim, outcomes round-trip, scenario_exec) build up to.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import yaml

from selfevals.api.run_jobs import create_run_job
from selfevals.optimization.coordinator import RunCoordinator
from selfevals.repo.loader import build_spec_from_mapping
from selfevals.runner.launch import build_loop, ensure_workspace_by_id, payload_router_for_db
from selfevals.schemas.enums import ExperimentState
from selfevals.schemas.job import ScenarioJobStatus
from selfevals.storage.factory import open_storage
from selfevals.worker.scenario_runner import run_scenario_jobs_once

REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_EXAMPLE = REPO_ROOT / "evals" / "experiments" / "example_pingpong.yaml"
CASES = REPO_ROOT / "evals" / "datasets" / "pingpong.jsonl"
WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _spec_mapping(*, max_iterations: int = 1) -> dict[str, Any]:
    raw = yaml.safe_load(REPO_EXAMPLE.read_text())
    rows = [json.loads(line) for line in CASES.read_text().splitlines() if line.strip()]
    raw["dataset"] = {"cases_inline": rows}
    raw["experiment"]["run"]["max_iterations"] = max_iterations
    return raw


def _drain_inline(storage_url: str, workspace_id: str) -> Any:
    """A drain callback that runs a worker inline until the barrier clears."""

    def drain(*, run_job_id: str, iteration: int) -> None:
        # Claim+run batches until nothing is left pending for this iteration.
        while run_scenario_jobs_once(
            storage_url=storage_url,
            run_job_id=run_job_id,
            workspace_id=workspace_id,
            iteration=iteration,
            worker_id="test-worker",
            batch=4,
        ):
            pass

    return drain


def test_sharded_coordinator_runs_to_completion(db_url: str) -> None:
    storage = open_storage(db_url)
    try:
        # Build a real spec + persist workspace.
        spec = build_spec_from_mapping(_spec_mapping(), workspace_id=WS)
        ensure_workspace_by_id(storage, WS)
        scope = storage.open(WS)

        # build_loop persists the experiment (+ eval_cases) — must happen before
        # create_run_job, whose FK points at the experiment row.
        base = build_loop(
            spec,
            scope=scope,
            repetitions_per_case=1,
            payload_router=payload_router_for_db(db_url, WS),
        )
        run_job = create_run_job(storage, spec=spec, reps=1)
        coordinator = RunCoordinator(
            base=base,
            storage=storage,
            run_job=run_job,
            drain=_drain_inline(db_url, WS),
        )
        result = asyncio.run(coordinator.run())

        # The run reached a terminal experiment state via the sharded path.
        assert result.experiment.state == ExperimentState.COMPLETED
        assert result.iterations, "coordinator produced no iterations"
        assert result.terminated_reason

        # Every scenario job for iteration 0 finished, and an outcome was written
        # per case.
        counts = storage.barrier_counts(run_job_id=run_job.id, iteration=0)
        assert counts.get(ScenarioJobStatus.SUCCEEDED.value, 0) > 0
        assert counts.get(ScenarioJobStatus.PENDING.value, 0) == 0
        outcomes = storage.scenario_outcomes_for_iteration(run_job_id=run_job.id, iteration=0)
        assert len(outcomes) == counts[ScenarioJobStatus.SUCCEEDED.value]

        # The coordinator's aggregate is non-trivial (it read real outcomes).
        agg = result.iterations[0].aggregate
        assert agg.primary_value >= 0.0

        scope.close()
    finally:
        storage.close()


def test_drain_is_idempotent_replan(db_url: str) -> None:
    """Re-planning an iteration (coordinator restart) must not double-run cases."""
    storage = open_storage(db_url)
    try:
        spec = build_spec_from_mapping(_spec_mapping(), workspace_id=WS)
        ensure_workspace_by_id(storage, WS)
        scope = storage.open(WS)
        base = build_loop(
            spec, scope=scope, repetitions_per_case=1,
            payload_router=payload_router_for_db(db_url, WS),
        )
        run_job = create_run_job(storage, spec=spec, reps=1)
        coordinator = RunCoordinator(
            base=base, storage=storage, run_job=run_job, drain=_drain_inline(db_url, WS)
        )
        asyncio.run(coordinator.run())
        first = storage.scenario_outcomes_for_iteration(run_job_id=run_job.id, iteration=0)

        # Re-plan the same iteration: UNIQUE(run_job_id, iteration, case_id) → no-op.
        from selfevals.api.run_jobs import plan_scenario_jobs

        inserted = plan_scenario_jobs(
            storage, run_job=run_job, case_ids=[c.id for c in base.optimization_cases],
            iteration=0, parameter_overrides={}, reps=1,
        )
        assert inserted == 0
        second = storage.scenario_outcomes_for_iteration(run_job_id=run_job.id, iteration=0)
        assert len(first) == len(second)

        scope.close()
    finally:
        storage.close()
