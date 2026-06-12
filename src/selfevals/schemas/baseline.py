"""BaselineRecord — the versioned baseline pointer for an experiment.

There is no `ExperimentRecord` carrying a baseline field; experiments live as
`IterationRecord`s linked by `experiment_id`. To version "the iteration the
regression gate compares against", we persist a small standalone entity that
points at one iteration of one experiment. It is a normal `BaseEntity`, so it
rides the generic SQLite `entities` table with no migration: a new
`entity_type` tag (`BaselineRecord`) and a JSON payload.

One baseline is "current" per experiment: `baseline set` writes a fresh record
(new id, new `created_at`) and the latest-by-`created_at` wins. Keeping every
write rather than mutating one row makes the baseline *history* queryable — you
can see when and to what the bar moved.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import Field

from selfevals.schemas._base import BaseEntity, NonEmptyStr


class BaselineRecord(BaseEntity):
    """A pointer to the iteration that is an experiment's current baseline."""

    _id_prefix: ClassVar[str] = "bl"

    experiment_id: NonEmptyStr
    iteration_id: NonEmptyStr
    """The `IterationRecord.id` selected as baseline."""
    iteration: int = Field(ge=0)
    """The iteration's ordinal, denormalized for human-readable listing."""
    primary_metric: NonEmptyStr
    primary_value: float
    """The baseline's primary metric value, snapshotted so `baseline show`
    needs no second lookup and the bar is auditable even if the iteration is
    later mutated."""
    macro_f1: float | None = None
    error_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    note: str | None = None
