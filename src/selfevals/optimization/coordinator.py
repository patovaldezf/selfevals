"""RunCoordinator: the sharded replacement for in-process iteration execution.

The coordinator IS a run job. It reuses the entire iteration bucle from
``OptimizationLoop`` (proposer → decision → convergence → record persistence) and
overrides only the one hook that differs: instead of running an iteration's cases
in-process, it seeds one scenario job per case, waits for workers to drain them
(the barrier), and aggregates the iteration from the ``scenario_outcomes`` rows
the workers wrote. So there is exactly one bucle and one grading path; only the
*execution substrate* changes.

The coordinator never executes a case itself — that would resurrect the
in-process path. Workers (``worker/scenario_runner``) do. To stay testable
without a live worker pool, a ``drain`` callback is invoked after planning each
iteration; production passes one that just nudges Redis, a test passes one that
runs a worker inline until the barrier clears.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Protocol

from selfevals.api.run_jobs import plan_scenario_jobs
from selfevals.optimization.loop import OptimizationLoop
from selfevals.optimization.scenario_outcomes import aggregate_iteration_from_storage

if TYPE_CHECKING:
    from selfevals.graders.base import GradeResult
    from selfevals.optimization.aggregator import IterationAggregate
    from selfevals.runner.executor import CaseRun
    from selfevals.schemas.iteration import Proposal
    from selfevals.schemas.job import RunJob
    from selfevals.storage.interface import StorageInterface

logger = logging.getLogger(__name__)

# How long to wait for an iteration's scenario jobs to finish, and how often to
# poll the barrier. Generous ceiling — a stuck worker is caught by the lease
# sweeper, not by the coordinator timing out mid-iteration.
_BARRIER_POLL_SECONDS = 0.2
_BARRIER_TIMEOUT_SECONDS = 3600.0


class IterationDrain(Protocol):
    """Drive an iteration's scenario jobs to completion.

    Called once per iteration AFTER the coordinator has planned (seeded) the
    scenario jobs. Production nudges the worker pool (Redis XADD) and returns;
    tests run a worker inline. Either way the coordinator then polls the barrier.
    """

    def __call__(self, *, run_job_id: str, iteration: int) -> None: ...


class RunCoordinator(OptimizationLoop):
    """An OptimizationLoop whose iterations execute as sharded scenario jobs."""

    def __init__(
        self,
        *,
        base: OptimizationLoop,
        storage: StorageInterface,
        run_job: RunJob,
        drain: IterationDrain,
    ) -> None:
        # Adopt the fully-wired base loop's state instead of re-running __init__
        # (which validates cases/graders and builds the executor). The coordinator
        # reuses the base's experiment/proposer/evaluator/cases/scope verbatim.
        self.__dict__.update(base.__dict__)
        self._storage = storage
        self._run_job = run_job
        self._run_job_id = run_job.id
        self._drain = drain

    async def _run_iteration(
        self, proposal: Proposal, *, iteration: int
    ) -> tuple[IterationAggregate, list[CaseRun], dict[str, list[list[GradeResult]]], list[str]]:
        """Seed scenario jobs for this iteration, await the barrier, aggregate.

        Overrides the in-process hook. Returns empty ``case_runs`` /
        ``persisted_run_ids`` because the workers own those (the reporter reads
        traces from storage in the sharded model); the ``aggregate`` is rebuilt
        from the persisted ``scenario_outcomes``.
        """
        # 1. Seed one scenario job per optimization-set case (idempotent).
        plan_scenario_jobs(
            self._storage,
            run_job=self._run_job,
            case_ids=[c.id for c in self._cases],
            iteration=iteration,
            parameter_overrides=proposal.parameters,
            reps=self._reps,
        )
        # 2. Hand off to workers, then wait until every case reaches terminal.
        # The drain runs OFF the event loop (workers are separate processes in
        # production; an inline test worker is sync and uses asyncio.run per case,
        # which cannot nest inside this loop). to_thread models that boundary.
        await asyncio.to_thread(self._drain, run_job_id=self._run_job_id, iteration=iteration)
        await self._await_barrier(iteration)
        # 3. Aggregate from the rows workers wrote — never from memory.
        aggregate = aggregate_iteration_from_storage(
            self._storage,
            run_job_id=self._run_job_id,
            iteration=iteration,
            primary_metric=self._experiment.target.primary.name,
            reliability_metrics=self._experiment.reliability.metrics,
            primary_grader=self._experiment.target.primary_grader,
        )
        # Collect the run_ids of traces the workers persisted this iteration so
        # the IterationRecord's `trace_run_ids` resolves (the trace viewer + the
        # /traces endpoint rely on it). case_runs stay empty — they live in
        # storage now, and the reporter rehydrates them on demand.
        traces = self._storage.traces_for_experiment_iteration(
            self._experiment.workspace_id, self._experiment.id, iteration
        )
        persisted_run_ids = [t.run.run_id for t in traces]
        return aggregate, [], {}, persisted_run_ids

    async def _await_barrier(self, iteration: int) -> None:
        """Poll until no scenario job for this iteration is still in flight."""
        waited = 0.0
        while waited < _BARRIER_TIMEOUT_SECONDS:
            counts = self._storage.barrier_counts(
                run_job_id=self._run_job_id, iteration=iteration
            )
            in_flight = (
                counts.get("pending", 0) + counts.get("claimed", 0) + counts.get("running", 0)
            )
            if in_flight == 0:
                return
            await asyncio.sleep(_BARRIER_POLL_SECONDS)
            waited += _BARRIER_POLL_SECONDS
        raise TimeoutError(
            f"iteration {iteration} barrier did not clear within "
            f"{_BARRIER_TIMEOUT_SECONDS}s (run_job={self._run_job_id})"
        )

    def close_executor(self) -> None:
        # The coordinator builds an executor via the base loop but never runs
        # cases through it; close it so the embedded OTLP receiver (if any) stops.
        super().close_executor()
