"""Durable run job state for worker-backed experiment execution."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field

from selfevals.schemas._base import BaseEntity, NonEmptyStr


class RunJobStatus(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DEAD_LETTERED = "dead_lettered"


class RunJob(BaseEntity):
    """Durable execution envelope for one HTTP-launched experiment run."""

    _id_prefix = "job"

    experiment_id: NonEmptyStr
    status: RunJobStatus = RunJobStatus.QUEUED
    attempt: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1)
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    last_error: str | None = None
    spec_payload: dict[str, Any]
    reps: int = Field(default=1, ge=1)

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            RunJobStatus.SUCCEEDED,
            RunJobStatus.FAILED,
            RunJobStatus.CANCELLED,
            RunJobStatus.DEAD_LETTERED,
        }

    @property
    def should_cancel(self) -> bool:
        return self.cancel_requested_at is not None

    def mark_cancel_requested(self, when: datetime) -> None:
        if not self.is_terminal and self.cancel_requested_at is None:
            self.cancel_requested_at = when

    def mark_leased(self, *, owner: str, lease_expires_at: datetime) -> None:
        self.status = RunJobStatus.LEASED
        self.lease_owner = owner
        self.lease_expires_at = lease_expires_at

    def mark_running(self, *, owner: str, lease_expires_at: datetime, started_at: datetime) -> None:
        self.status = RunJobStatus.RUNNING
        self.lease_owner = owner
        self.lease_expires_at = lease_expires_at
        self.started_at = self.started_at or started_at

    def renew_lease(self, *, owner: str, lease_expires_at: datetime) -> None:
        self.lease_owner = owner
        self.lease_expires_at = lease_expires_at

    def mark_succeeded(self, when: datetime) -> None:
        self.status = RunJobStatus.SUCCEEDED
        self.finished_at = when
        self.lease_owner = None
        self.lease_expires_at = None
        self.last_error = None

    def mark_cancelled(self, when: datetime) -> None:
        self.status = RunJobStatus.CANCELLED
        self.finished_at = when
        self.lease_owner = None
        self.lease_expires_at = None

    def mark_failed_or_dead_lettered(self, *, error: str, when: datetime) -> Literal["retry", "dead"]:
        self.last_error = error
        self.lease_owner = None
        self.lease_expires_at = None
        if self.attempt >= self.max_attempts:
            self.status = RunJobStatus.DEAD_LETTERED
            self.finished_at = when
            return "dead"
        self.status = RunJobStatus.FAILED
        return "retry"


class ScenarioJobStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD_LETTERED = "dead_lettered"
    CANCELLED = "cancelled"


class ScenarioJob(BaseEntity):
    """One unit of sharded work: a single (iteration, case) of a run.

    A coordinator (the parent ``RunJob``) seeds one of these per case per
    iteration; workers claim them with ``FOR UPDATE SKIP LOCKED`` and run the
    case + grading. ``lease_until``/``worker_id`` mirror ``RunJob``'s
    lease/owner so the same heartbeat + sweeper reap a dead worker's claim.
    """

    _id_prefix = "scj"

    run_job_id: NonEmptyStr
    experiment_id: NonEmptyStr
    iteration: int = Field(default=0, ge=0)
    case_id: NonEmptyStr
    reps: int = Field(default=1, ge=1)
    status: ScenarioJobStatus = ScenarioJobStatus.PENDING
    attempt: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1)
    lease_until: datetime | None = None
    worker_id: str | None = None
    error: str | None = None
    parameter_overrides: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            ScenarioJobStatus.SUCCEEDED,
            ScenarioJobStatus.FAILED,
            ScenarioJobStatus.DEAD_LETTERED,
            ScenarioJobStatus.CANCELLED,
        }

    def mark_claimed(self, *, worker_id: str, lease_until: datetime) -> None:
        self.status = ScenarioJobStatus.CLAIMED
        self.worker_id = worker_id
        self.lease_until = lease_until

    def mark_running(self, *, worker_id: str, lease_until: datetime, started_at: datetime) -> None:
        self.status = ScenarioJobStatus.RUNNING
        self.worker_id = worker_id
        self.lease_until = lease_until
        self.started_at = self.started_at or started_at

    def mark_succeeded(self, when: datetime) -> None:
        self.status = ScenarioJobStatus.SUCCEEDED
        self.finished_at = when
        self.worker_id = None
        self.lease_until = None
        self.error = None

    def mark_cancelled(self, when: datetime) -> None:
        self.status = ScenarioJobStatus.CANCELLED
        self.finished_at = when
        self.worker_id = None
        self.lease_until = None

    def mark_failed_or_dead_lettered(self, *, error: str, when: datetime) -> Literal["retry", "dead"]:
        self.error = error
        self.worker_id = None
        self.lease_until = None
        if self.attempt >= self.max_attempts:
            self.status = ScenarioJobStatus.DEAD_LETTERED
            self.finished_at = when
            return "dead"
        self.status = ScenarioJobStatus.PENDING  # back to the claimable frontier
        return "retry"
