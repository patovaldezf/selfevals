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
