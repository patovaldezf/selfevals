"""Shared helper: run an ExperimentSpec to completion through the sharded path.

The optimization-loop tests used to build an ``OptimizationLoop`` with a closure
adapter and call ``.run()`` in-process. That path no longer runs in production —
every run shards. This helper is the test-side equivalent of what the CLI / API
launcher do: persist the experiment + a run job, wrap the built loop in a
``RunCoordinator``, and drive it with an in-process self-drain (one worker, same
process). It returns the ``OptimizationResult`` reconstructed by the coordinator,
so a migrated test asserts on the same metrics/decisions as before — but over the
real serialize → claim → execute → aggregate-from-storage path.

Agents MUST be declared by entrypoint (import path) in the spec, since the worker
resolves them by path. See ``selfevals.examples.testkit`` for the serializable
agents the suite uses.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from selfevals.api.run_jobs import create_run_job
from selfevals.optimization.coordinator import RunCoordinator
from selfevals.repo.loader import build_spec_from_mapping
from selfevals.runner.launch import build_loop, ensure_workspace_by_id, payload_router_for_db
from selfevals.storage.factory import open_storage
from selfevals.worker.scenario_runner import drain_self

if TYPE_CHECKING:
    from selfevals.optimization.loop import OptimizationResult
    from selfevals.repo.loader import ExperimentSpec

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def make_case(
    *, must_include: str = "pong", holdout: bool = False, name: str = "say pong"
) -> dict[str, Any]:
    """A minimal valid inline EvalCase mapping (mirrors evals/datasets/pingpong.jsonl)."""
    row: dict[str, Any] = {
        "name": name,
        "task_type": "echo",
        "input": {"messages": [{"role": "user", "content": "ping"}]},
        "taxonomy": {
            "level": "final_response",
            "feature": {"primary": "commerce.product_resolution"},
            "source": {"type": "handcrafted"},
            "ground_truth": {"methods": ["exact_match"]},
            "dataset_type": "capability",
        },
        "expected": {"must_include": [must_include]},
    }
    if holdout:
        row["holdout"] = True
    return row


def make_spec(
    *,
    workspace_id: str = WS,
    entrypoint: str = "selfevals.examples.testkit:pong_by_level",
    cases: list[dict[str, Any]] | None = None,
    search_space: dict[str, Any] | None = None,
    max_iterations: int = 4,
    proposer: str = "grid",
    primary_value: float = 0.9,
    persist_traces: str = "failed",
    convergence: dict[str, Any] | None = None,
    reliability: list[str] | None = None,
    judge_entrypoint: str | None = None,
    graders: list[dict[str, Any]] | None = None,
    error_analysis: dict[str, Any] | None = None,
) -> ExperimentSpec:
    """Build an ExperimentSpec with a SERIALIZABLE agent (declared by entrypoint).

    The compact builder the migrated loop tests use instead of hand-assembling an
    Experiment + Executor with a closure adapter. Defaults to the pong/miss agent
    and a single ``must_include: [pong]`` case so most tests pass only what they
    vary (search space, proposer, iterations).
    """
    case_rows = cases if cases is not None else [make_case()]
    mapping: dict[str, Any] = {
        "workspace": workspace_id,
        "experiment": {
            "name": "exp",
            "goal": "exp",
            "mode": "handoff",
            "taxonomy": {
                "target_features": ["commerce.product_resolution"],
                "dataset_types": ["capability"],
            },
            "datasets": {"optimization": {"id": "ds_x", "version": 1}},
            "target": {"primary": {"name": "pass@1", "operator": ">=", "value": primary_value}},
            "editable": {"prompt": True, "model_params": True},
            "frozen": {
                "fleet": {"id": "flt_x"},
                "agents": [{"id": "ag_x"}],
                "datasets": [{"id": "ds_y"}],
            },
            "proposer": {"strategy": proposer},
            "search_space": {"model_params": search_space or {}},
            "run": {
                "sandbox": "mock",
                "max_iterations": max_iterations,
                "convergence": convergence or {"min_delta": 1e-6, "patience": 2},
                "persist_traces": persist_traces,
            },
            "reliability": {"metrics": reliability or ["pass@1"]},
        },
        "dataset": {"cases_inline": case_rows},
        "agent": {"entrypoint": entrypoint},
    }
    if graders is not None:
        mapping["experiment"]["graders"] = graders
    if judge_entrypoint is not None:
        mapping["agent"]["judge_entrypoint"] = judge_entrypoint
    if error_analysis is not None:
        mapping["experiment"]["error_analysis"] = error_analysis
    return build_spec_from_mapping(mapping, workspace_id=workspace_id)


def run_sharded_to_completion(
    *, db_url: str, spec: ExperimentSpec, reps: int = 1
) -> OptimizationResult:
    """Persist + run ``spec`` to a terminal experiment state via sharding.

    Opens its own storage (the coordinator and the self-drain worker each need a
    connection), builds the loop (which persists the experiment + eval_cases),
    creates the run-job coordinator, and runs it with an in-process self-drain.
    The caller owns ``db_url``; this helper does not close anything the fixture
    owns. Returns the coordinator's ``OptimizationResult``.
    """
    storage = open_storage(db_url)
    workspace_id = spec.workspace_id
    try:
        ensure_workspace_by_id(storage, workspace_id)
        scope = storage.open(workspace_id)
        # build_loop persists the experiment + eval_cases — must precede
        # create_run_job, whose FK points at the experiment row.
        base = build_loop(
            spec,
            scope=scope,
            repetitions_per_case=reps,
            payload_router=payload_router_for_db(db_url, workspace_id),
        )
        run_job = create_run_job(storage, spec=spec, reps=reps)
        coordinator = RunCoordinator(
            base=base,
            storage=storage,
            run_job=run_job,
            drain=drain_self(db_url, workspace_id),
        )
        try:
            return asyncio.run(coordinator.run())
        finally:
            scope.close()
    finally:
        storage.close()
