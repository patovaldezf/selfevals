"""HTTP launch path for `POST .../experiments/run` (F1).

Turns a `RunExperimentRequest` into a queued background run, reusing the exact
canonical wiring the CLI uses (`runner.launch.build_loop`). The handler is sync
and returns 202 immediately; the loop — which drives LLM calls and can take
minutes — runs on a daemon thread with its own SQLite connection and its own
`asyncio.run`, so it never blocks the FastAPI event loop. (The span broker
already publishes cross-thread via `call_soon_threadsafe`, so SSE keeps working.)

State follows the loop: the handler persists the experiment as soon as it
validates (so a `GET` sees it right away), then the thread drives it through
running → completed. On failure the experiment is moved to `aborted` (there is
no `failed` state for an experiment) and the exception is logged.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import HTTPException

from selfevals._errors import SelfEvalsUserError
from selfevals.api.broker import get_broker
from selfevals.api.recorder_sink import BrokerSpanSink
from selfevals.api.run_jobs import (
    DEFAULT_LEASE_SECONDS,
    RunJobQueue,
    create_run_job,
    heartbeat_run_job_lease,
    lease_run_job,
    mark_run_job_cancelled,
    mark_run_job_failed,
    mark_run_job_running,
    mark_run_job_succeeded,
)
from selfevals.api.run_queue import configured_run_queue
from selfevals.api.schemas import RunExperimentRequest, RunExperimentResponse
from selfevals.cli import _friendly
from selfevals.optimization.coordinator import RunCoordinator
from selfevals.repo.loader import (
    ExperimentSpec,
    LoaderError,
    RefDatasetSource,
    build_spec_from_mapping,
    deserialize_experiment_spec,
)
from selfevals.runner.launch import (
    build_loop,
    ensure_workspace,
    payload_router_for_db,
    trace_sampling_override,
)
from selfevals.schemas.enums import ExperimentState
from selfevals.schemas.experiment import Experiment
from selfevals.storage.factory import open_storage
from selfevals.worker.scenario_runner import drain_self

logger = logging.getLogger(__name__)

# States from which a fresh launch would collide with an in-flight run.
_ACTIVE_STATES = {
    ExperimentState.QUEUED,
    ExperimentState.RUNNING,
    ExperimentState.PAUSED,
}


def launch_experiment_run(
    *,
    storage_url: str,
    workspace_id: str,
    body: RunExperimentRequest,
) -> RunExperimentResponse:
    """Validate, persist, and kick off a background run. Returns the 202 body.

    Raises `HTTPException` for the user-facing error paths:
    * 422 — spec does not validate / yields zero cases / bad source combination.
    * 409 — the target experiment already has an active run.
    """
    spec = _load_spec(workspace_id=workspace_id, body=body)
    _apply_overrides(spec, body)

    storage = open_storage(storage_url)
    try:
        ensure_workspace(storage, spec)
        with storage.open(spec.workspace_id) as scope:
            if scope.exists(Experiment, spec.experiment.id):
                existing = scope.get_entity(Experiment, spec.experiment.id)
                assert isinstance(existing, Experiment)
                if existing.state in _ACTIVE_STATES:
                    raise HTTPException(
                        status_code=409,
                        detail=f"experiment {spec.experiment.id} already has an active run",
                    )
            # Persist up front so a poll right after the 202 finds the
            # experiment in its starting state.
            scope.put_entity(spec.experiment)
        reps = body.reps if body.reps is not None else 1
        job = create_run_job(storage, spec=spec, reps=reps)
    finally:
        storage.close()

    queue = configured_run_queue()
    if queue is not None:
        queue.enqueue(job)
        dispatch = "redis-worker"
        # A job enqueued with no worker consuming it sits in the stream
        # silently and the experiment never leaves `draft`. Surface that the
        # moment it happens instead of forcing an `XINFO GROUPS` autopsy.
        # `active_consumers` counts only live consumers (by idle), so a crashed
        # worker's ghost registration doesn't suppress the warning. It returns
        # None on any Redis error and never fails the launch. False positive to
        # accept: a lone worker busy in a >60s job looks idle, so a launch in
        # that window may warn even though a worker exists — informational only.
        if queue.active_consumers() == 0:
            logger.warning(
                "experiment %s queued to redis (%s) but no worker is consuming — "
                "start one with 'selfevals worker runs' (and make sure it points at "
                "the same SELFEVALS_REDIS_URL, including the DB number)",
                spec.experiment.id,
                queue.redis_label,
            )
    else:
        dispatch = "in-process-thread"
        thread = threading.Thread(
            target=_run_in_thread,
            kwargs={"storage_url": storage_url, "workspace_id": spec.workspace_id, "job_id": job.id},
            name=f"run-{spec.experiment.id}",
            daemon=True,
        )
        thread.start()

    return RunExperimentResponse(
        experiment_id=spec.experiment.id,
        workspace_id=spec.workspace_id,
        state=str(spec.experiment.state),
        job_id=job.id,
        dispatch=dispatch,
    )


def _load_spec(*, workspace_id: str, body: RunExperimentRequest) -> ExperimentSpec:
    """Build the spec from disk or inline body. Loader errors → 422.

    `workspace_id` from the path always wins over any `workspace:` in the spec,
    so a POST to `/workspaces/{ws}/...` is authoritative about where the run
    lands.
    """
    try:
        if body.spec_inline is not None:
            spec = build_spec_from_mapping(body.spec_inline, workspace_id=workspace_id)
        else:
            assert body.spec_path is not None  # guaranteed by the request validator
            spec = _friendly.load_spec(body.spec_path, workspace_id=workspace_id)
    except (SelfEvalsUserError, LoaderError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if body.dataset_id is not None:
        spec = _override_dataset(spec, body.dataset_id)
    return spec


def _override_dataset(spec: ExperimentSpec, dataset_id: str) -> ExperimentSpec:
    """Swap the spec's dataset source for a reference to `dataset_id`.

    `ExperimentSpec` is a frozen dataclass, so we rebuild it via `replace`. The
    referenced dataset is resolved at launch (`build_loop`), which also hydrates
    the cases — so we clear `cases` here, mirroring how the loader leaves a
    ref-sourced spec empty until storage fills it."""
    from dataclasses import replace

    from selfevals.schemas._base import EntityRef

    return replace(
        spec,
        dataset_source=RefDatasetSource(ref=EntityRef(id=dataset_id)),
        cases=[],
    )


def _apply_overrides(spec: ExperimentSpec, body: RunExperimentRequest) -> None:
    if body.max_iterations is not None:
        spec.experiment.run.max_iterations = body.max_iterations
    # Precedence: explicit request field > SELFEVALS_TRACE_SAMPLING env > spec.
    if body.persist_traces is not None:
        spec.experiment.run.persist_traces = body.persist_traces
    else:
        env_policy = trace_sampling_override()
        if env_policy is not None:
            spec.experiment.run.persist_traces = env_policy


def _run_in_thread(*, storage_url: str, workspace_id: str, job_id: str) -> None:
    execute_run_job(storage_url=storage_url, workspace_id=workspace_id, job_id=job_id, owner="api-thread")


@contextmanager
def _lease_heartbeat(
    *, storage_url: str, workspace_id: str, job_id: str, owner: str
) -> Iterator[None]:
    """Renew the job's lease in the background while the run executes.

    Without this, a run longer than ``DEFAULT_LEASE_SECONDS`` lets its own lease
    lapse, and the sweeper would reap a perfectly healthy run as a zombie. A
    daemon thread re-touches the lease every ``DEFAULT_LEASE_SECONDS / 3`` so two
    consecutive misses still beat expiry. The heartbeat opens its own storage
    (its own DB connection): the run owns this thread's connection via
    ``asyncio.run`` below, and psycopg connections are not shareable across
    threads. Failures are swallowed — a missed beat is recoverable (next beat or
    sweeper retry), and the heartbeat must never crash the run.
    """
    interval = max(1.0, DEFAULT_LEASE_SECONDS / 3)
    stop = threading.Event()

    def _beat() -> None:
        hb_storage = open_storage(storage_url)
        try:
            while not stop.wait(interval):
                try:
                    renewed = heartbeat_run_job_lease(
                        hb_storage, workspace_id=workspace_id, job_id=job_id, owner=owner
                    )
                except Exception:  # pragma: no cover - best-effort, never fatal
                    logger.warning("lease heartbeat failed for job %s", job_id, exc_info=True)
                    continue
                if not renewed:
                    # Job reached a terminal state (or vanished) — stop beating.
                    return
        finally:
            hb_storage.close()

    thread = threading.Thread(target=_beat, name=f"lease-heartbeat-{job_id}", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=5.0)


def execute_run_job(
    *,
    storage_url: str,
    workspace_id: str,
    job_id: str,
    owner: str,
    queue: RunJobQueue | None = None,
) -> bool:
    """Background worker: own storage + own event loop. Never blocks FastAPI.

    The loop drives `spec.experiment.state` (draft → … → completed) and now
    persists the experiment row at each transition via its `scope`, so a polling
    `GET` follows progress without anything extra here. On failure we still move
    it to `aborted` ourselves (the loop has no failure transition).

    Live streaming: we attach a `BrokerSpanSink` so every span the run produces
    is fanned out to `/stream` subscribers as it happens. The sink publishes via
    `call_soon_threadsafe` onto FastAPI's loop (bound at app startup), so it
    crosses from this worker thread into the SSE loop without blocking either.
    The broker is a process-wide singleton; if `serve` never bound a loop (e.g.
    a bare run with no SSE consumers) the sink degrades to a silent no-op.
    """
    storage = open_storage(storage_url)
    with lease_run_job(storage, workspace_id=workspace_id, job_id=job_id, owner=owner) as job:
        if job is None:
            storage.close()
            return False
        if job.should_cancel:
            mark_run_job_cancelled(storage, job=job)
            storage.close()
            return True
        job = mark_run_job_running(storage, job=job, owner=owner)
        spec = deserialize_experiment_spec(job.spec_payload)
        scope = None
        try:
            scope = storage.open(spec.workspace_id)
            span_sink = BrokerSpanSink(get_broker())
            # Same object store the `/payloads` endpoint reads from, so trace
            # prompts/responses offloaded here resolve there.
            payload_router = payload_router_for_db(storage_url, spec.workspace_id)
            loop = build_loop(
                spec,
                scope=scope,
                repetitions_per_case=job.reps,
                span_sink=span_sink,
                payload_router=payload_router,
            )
            # The run-job IS the coordinator: it shards each iteration into
            # scenario jobs and aggregates from storage. The drain runs this same
            # process's worker over the iteration's frontier (single worker / a
            # local run); with N workers the claim SKIP LOCKED splits the work
            # and this drain just races them.
            coordinator = RunCoordinator(
                base=loop,
                storage=storage,
                run_job=job,
                drain=drain_self(storage_url, spec.workspace_id),
            )
            with _lease_heartbeat(
                storage_url=storage_url,
                workspace_id=job.workspace_id,
                job_id=job.id,
                owner=owner,
            ):
                asyncio.run(coordinator.run())
            mark_run_job_succeeded(storage, job=job)
            return True
        except Exception as exc:
            logger.exception("experiment run failed: %s", spec.experiment.id)
            job, should_retry = mark_run_job_failed(storage, job=job, error=str(exc))
            if should_retry and queue is not None:
                queue.requeue(job)
            return False
        finally:
            if scope is not None:
                scope.close()
            storage.close()
