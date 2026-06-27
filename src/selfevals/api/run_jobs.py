"""Durable run job helpers for HTTP-launched experiment runs."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Protocol

from selfevals._internal.time import utc_now
from selfevals.repo.loader import ExperimentSpec, serialize_experiment_spec
from selfevals.schemas.enums import ExperimentState
from selfevals.schemas.experiment import Experiment
from selfevals.schemas.job import RunJob, RunJobStatus
from selfevals.storage.errors import EntityNotFoundError
from selfevals.storage.interface import ListFilter, StorageInterface, WorkspaceScope

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_LEASE_SECONDS = 300


class RunJobQueue(Protocol):
    def enqueue(self, job: RunJob) -> None: ...

    def requeue(self, job: RunJob) -> None: ...


def create_run_job(
    storage: StorageInterface,
    *,
    spec: ExperimentSpec,
    reps: int,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> RunJob:
    job = RunJob(
        id=RunJob.make_id(),
        workspace_id=spec.workspace_id,
        experiment_id=spec.experiment.id,
        max_attempts=max_attempts,
        spec_payload=serialize_experiment_spec(spec),
        reps=reps,
    )
    with storage.open(spec.workspace_id) as scope:
        scope.put_entity(job)
    return job


def get_run_job(
    storage: StorageInterface, *, workspace_id: str, job_id: str
) -> RunJob | None:
    # Only a genuinely-absent job is None. A WorkspaceMismatchError (job exists
    # under another workspace) or any other storage error is a real fault — the
    # sweeper and launcher rely on this, so swallowing those as "not found" would
    # silently mask corruption (see TECHNICAL_DEBT.md gap #16).
    with storage.open(workspace_id) as scope:
        try:
            job = scope.get_entity(RunJob, job_id)
        except EntityNotFoundError:
            return None
    assert isinstance(job, RunJob)
    return job


def latest_run_job_for_experiment(
    storage: StorageInterface, *, workspace_id: str, experiment_id: str
) -> RunJob | None:
    with storage.open(workspace_id) as scope:
        jobs = [
            job
            for job in scope.list_entities(
                RunJob,
                ListFilter(where={"experiment_id": experiment_id}, order_by="updated_at"),
            )
            if isinstance(job, RunJob)
        ]
    return jobs[0] if jobs else None


def request_cancel_run_job(
    storage: StorageInterface, *, workspace_id: str, experiment_id: str
) -> RunJob | None:
    now = utc_now()
    with storage.open(workspace_id) as scope:
        jobs = [
            job
            for job in scope.list_entities(
                RunJob,
                ListFilter(where={"experiment_id": experiment_id}, order_by="updated_at"),
            )
            if isinstance(job, RunJob)
        ]
        if not jobs:
            return None
        job = jobs[0]
        job.mark_cancel_requested(now)
        if job.status == RunJobStatus.QUEUED:
            job.mark_cancelled(now)
            _abort_experiment_in_scope(scope, experiment_id)
        scope.put_entity(job)
        return job


@contextmanager
def lease_run_job(
    storage: StorageInterface,
    *,
    workspace_id: str,
    job_id: str,
    owner: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> Iterator[RunJob | None]:
    """Acquire a best-effort durable lease and yield the leased job."""
    now = utc_now()
    expires = now + timedelta(seconds=lease_seconds)
    with storage.open(workspace_id) as scope:
        try:
            job = scope.get_entity(RunJob, job_id)
        except EntityNotFoundError:
            # A missing job is unleasable (already acked/cleaned); anything else
            # (workspace mismatch, storage fault) must surface, not present as an
            # empty lease (see TECHNICAL_DEBT.md gap #16).
            yield None
            return
        assert isinstance(job, RunJob)
        if job.is_terminal:
            yield None
            return
        if job.lease_expires_at is not None and job.lease_expires_at > now:
            yield None
            return
        job.attempt += 1
        job.mark_leased(owner=owner, lease_expires_at=expires)
        scope.put_entity(job)
    yield job


def mark_run_job_running(
    storage: StorageInterface,
    *,
    job: RunJob,
    owner: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> RunJob:
    now = utc_now()
    job.mark_running(
        owner=owner,
        lease_expires_at=now + timedelta(seconds=lease_seconds),
        started_at=now,
    )
    with storage.open(job.workspace_id) as scope:
        scope.put_entity(job)
    return job


def renew_run_job_lease(
    storage: StorageInterface,
    *,
    job: RunJob,
    owner: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> RunJob:
    job.renew_lease(owner=owner, lease_expires_at=utc_now() + timedelta(seconds=lease_seconds))
    with storage.open(job.workspace_id) as scope:
        scope.put_entity(job)
    return job


def heartbeat_run_job_lease(
    storage: StorageInterface,
    *,
    workspace_id: str,
    job_id: str,
    owner: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> bool:
    """Renew a running job's lease without a version bump (the heartbeat path).

    Unlike ``renew_run_job_lease`` (which round-trips the entity through
    ``put_entity`` and its version-CAS), this issues a direct unversioned UPDATE
    of the lease columns. The in-flight run holds a stale in-memory copy of the
    row, so a CAS here would raise ``OptimisticConcurrencyError``; the lease is
    operational metadata, not versioned domain state. No-op once the job is
    terminal. Returns True if a row was renewed.
    """
    return storage.touch_run_job_lease(
        workspace_id=workspace_id,
        job_id=job_id,
        owner=owner,
        lease_expires_at=utc_now() + timedelta(seconds=lease_seconds),
    )


def mark_run_job_succeeded(storage: StorageInterface, *, job: RunJob) -> RunJob:
    job.mark_succeeded(utc_now())
    with storage.open(job.workspace_id) as scope:
        scope.put_entity(job)
    return job


def mark_run_job_cancelled(storage: StorageInterface, *, job: RunJob) -> RunJob:
    now = utc_now()
    job.mark_cancelled(now)
    with storage.open(job.workspace_id) as scope:
        _abort_experiment_in_scope(scope, job.experiment_id)
        scope.put_entity(job)
    return job


def mark_run_job_failed(
    storage: StorageInterface,
    *,
    job: RunJob,
    error: str,
) -> tuple[RunJob, bool]:
    outcome = job.mark_failed_or_dead_lettered(error=error, when=utc_now())
    with storage.open(job.workspace_id) as scope:
        if outcome == "dead":
            _abort_experiment_in_scope(scope, job.experiment_id)
        scope.put_entity(job)
    return job, outcome == "retry"


def _abort_experiment_in_scope(scope: WorkspaceScope, experiment_id: str) -> None:
    if not scope.exists(Experiment, experiment_id):
        return
    exp = scope.get_entity(Experiment, experiment_id)
    if not isinstance(exp, Experiment):
        return
    if exp.state in {ExperimentState.COMPLETED, ExperimentState.ABORTED, ExperimentState.SUPERSEDED}:
        return
    exp.transition_to(ExperimentState.ABORTED)
    scope.put_entity(exp)
