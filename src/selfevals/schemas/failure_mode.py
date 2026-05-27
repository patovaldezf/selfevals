"""FailureMode — one entry in a workspace's failure-mode taxonomy.

The taxonomy is the source of truth for "what failure modes exist" in a
workspace. Each mode has a machine-stable `id` (fm_…) and a human-stable
`slug`, so the same failure can be tracked across every experiment and
iteration forever — that stable identity is what turns error clustering into a
continuous-improvement loop rather than a per-run snapshot.

Modes are born CANDIDATE (proposed by an analysis agent, or seeded), promoted
to OFFICIAL by a human (the only status that feeds the proposer), and RETIRED
when they no longer apply. Two candidates that turn out to be the same mode are
merged via `superseded_by` — history is preserved, never deleted.

See docs/spec/error_analysis_design.md §3.
"""

from __future__ import annotations

import re
from typing import ClassVar

from pydantic import Field, field_validator

from selfevals.schemas._base import BaseEntity, NonEmptyStr, SelfEvalsModel
from selfevals.schemas.enums import FailureModeStatus

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,62}$")


class FailureModeExample(SelfEvalsModel):
    """Evidence linking a concrete trace to a failure mode.

    `quote_*` is the payload-routed snippet that justified the assignment;
    `note` is the open-coding observation that led to it (operational
    methodology: code the first failure, then group — §2 of the design doc).
    """

    trace_id: NonEmptyStr
    quote_pointer: str | None = None
    quote_hash: str | None = None
    note: str | None = None


class FailureMode(BaseEntity):
    _id_prefix: ClassVar[str] = "fm"

    slug: NonEmptyStr
    """Human-stable handle, e.g. 'invented_price'. Unique within a workspace;
    enforcement of uniqueness lives in the push/ingest path, not here."""

    title: NonEmptyStr
    definition: NonEmptyStr
    """The testable axial-coding definition — what distinguishes this mode from
    its neighbours. An agent classifies traces against this text."""

    status: FailureModeStatus = FailureModeStatus.CANDIDATE
    parent_mode_id: str | None = None
    """Optional hierarchy link: a subcategory points at its top-level mode."""

    examples: list[FailureModeExample] = Field(default_factory=list)
    proposed_by: NonEmptyStr = "seed"
    """Provenance: 'seed' | 'agent:<name>' | 'human:<name>'."""

    first_seen_iteration: int | None = Field(default=None, ge=0)
    superseded_by: str | None = None
    """Set when this mode was merged into another; readers should follow it."""

    @field_validator("slug")
    @classmethod
    def _slug_pattern(cls, value: str) -> str:
        if not _SLUG_RE.match(value):
            raise ValueError(
                "failure-mode slug must be 1-63 chars: lowercase letters, digits, "
                f"or '_', starting alphanumeric (got {value!r})"
            )
        return value
