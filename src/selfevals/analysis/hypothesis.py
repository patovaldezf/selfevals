"""HypothesisRecord — a testable change targeting a failure mode.

Produced by error analysis (the `hypotheses` block of an AnalysisResult) and
stored as a workspace entity linked to the experiment. The proposer consults
these to target a specific mode in the next iteration; selfevals does not run
them automatically. See docs/spec/error_analysis_design.md §7.
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import Field

from selfevals.schemas._base import BaseEntity, NonEmptyStr


class HypothesisRecord(BaseEntity):
    _id_prefix: ClassVar[str] = "hyp"

    experiment_id: NonEmptyStr
    targets_mode_slug: NonEmptyStr
    statement: NonEmptyStr
    suggested_parameters: dict[str, Any] = Field(default_factory=dict)
    consumed_by_iteration: int | None = Field(default=None, ge=0)
    """Set once a proposer has used this hypothesis, so it isn't re-applied."""
