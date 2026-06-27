"""Per-case worker: claim scenario jobs, run+grade them, persist outcomes.

This is the sharded execution path. A worker claims a batch of pending scenario
jobs for one (run_job, iteration), builds the run's loop ONCE (reusing
``build_loop`` for correct adapter/executor/grader wiring), and drives each case
through ``loop.run_scenario`` — the exact same execute+grade path the in-process
loop uses. Each finished case writes a ``scenario_outcomes`` row (the relational
CaseOutcome the coordinator aggregates from) and the scenario job is finalized.

Caching the built loop per run_job means the heavy spec deserialization + adapter
wiring happens once per drain, not once per case.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from selfevals._internal.ids import new_prefixed_id
from selfevals._internal.time import utc_now
from selfevals.api.run_jobs import DEFAULT_LEASE_SECONDS, get_run_job
from selfevals.optimization.scenario_outcomes import case_outcome_to_fields
from selfevals.repo.loader import deserialize_experiment_spec
from selfevals.runner.launch import build_loop, payload_router_for_db
from selfevals.schemas.eval_case import EvalCase
from selfevals.schemas.job import ScenarioJob
from selfevals.storage.factory import open_storage

if TYPE_CHECKING:
    from selfevals.optimization.loop import OptimizationLoop
    from selfevals.storage.interface import StorageInterface, WorkspaceScope

logger = logging.getLogger(__name__)

DEFAULT_BATCH = 4


def run_scenario_jobs_once(
    *,
    storage_url: str,
    run_job_id: str,
    workspace_id: str,
    iteration: int,
    worker_id: str,
    batch: int = DEFAULT_BATCH,
) -> int:
    """Claim and run one batch of scenario jobs for an iteration. Returns count.

    Loads the run's spec + loop once, then for each claimed job: load the case,
    run+grade it, persist trace(s) and the CaseOutcome, finalize the job. A case
    that throws is marked failed/retry rather than killing the batch.
    """
    storage = open_storage(storage_url)
    try:
        run_job = get_run_job(storage, workspace_id=workspace_id, job_id=run_job_id)
        if run_job is None:
            return 0
        claimed = storage.claim_scenario_jobs(
            run_job_id=run_job_id,
            iteration=iteration,
            worker_id=worker_id,
            lease_until=utc_now() + timedelta(seconds=DEFAULT_LEASE_SECONDS),
            batch=batch,
        )
        if not claimed:
            return 0

        spec = deserialize_experiment_spec(run_job.spec_payload)
        scope = storage.open(workspace_id)
        payload_router = payload_router_for_db(storage_url, workspace_id)
        loop = build_loop(
            spec,
            scope=scope,
            repetitions_per_case=run_job.reps,
            payload_router=payload_router,
        )
        try:
            processed = 0
            for sj in claimed:
                _run_one_scenario_job(storage, scope, loop, sj)
                processed += 1
            return processed
        finally:
            loop.close_executor()
            scope.close()
    finally:
        storage.close()


def _run_one_scenario_job(
    storage: StorageInterface,
    scope: WorkspaceScope,
    loop: OptimizationLoop,
    sj: ScenarioJob,
) -> None:
    """Run one scenario job end to end, persisting its outcome or failure."""
    now = utc_now()
    try:
        case = scope.get_entity(EvalCase, sj.case_id)
        assert isinstance(case, EvalCase)
        _, outcome = asyncio.run(
            loop.run_scenario(
                case, iteration=sj.iteration, parameter_overrides=sj.parameter_overrides
            )
        )
        storage.write_scenario_outcome(
            outcome_id=new_prefixed_id("sco"),
            workspace_id=sj.workspace_id,
            run_job_id=sj.run_job_id,
            scenario_job_id=sj.id,
            experiment_id=sj.experiment_id,
            iteration=sj.iteration,
            fields=case_outcome_to_fields(outcome),
            now=now,
        )
        storage.finalize_scenario_job(
            job_id=sj.id, status="succeeded", error=None, finished_at=now
        )
    except Exception as exc:  # one bad case must not kill the batch
        logger.exception("scenario job failed: %s (case=%s)", sj.id, sj.case_id)
        # The claim already bumped `attempt`; mark_* sets status to 'pending'
        # (retry, back on the claimable frontier) or 'dead_lettered'. Persist it.
        sj.mark_failed_or_dead_lettered(error=str(exc), when=now)
        storage.finalize_scenario_job(
            job_id=sj.id, status=sj.status.value, error=str(exc), finished_at=now
        )
