"""Annotation: a human-supplied judgement on a case+trace pair.

Annotations feed grader calibration and human-judgment ground truth. In MVP
we accept `labels` as a free dict (no Rubric registry yet) and `rubric_version`
as optional — validation against a versioned rubric will land when the rubric
registry does (post-MVP).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

from pydantic import Field, model_validator

from selfeval.schemas._base import BaseEntity, NonEmptyStr, SelfEvalModel


class AnnotationLabels(SelfEvalModel):
    """Free-form labels until a rubric registry exists.

    Stored as an opaque dict so callers can encode any schema (pass/fail,
    rubric scores, pairwise preference, etc.). The optional `rubric_version`
    is what a future rubric registry will use to validate `data`.
    """

    rubric_version: NonEmptyStr | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class Annotation(BaseEntity):
    _id_prefix: ClassVar[str] = "ann"

    case_id: NonEmptyStr
    trace_id: NonEmptyStr | None = None
    """None when annotating a case in isolation (e.g. ground-truth seed)."""

    annotator_id: NonEmptyStr
    labels: AnnotationLabels = Field(default_factory=AnnotationLabels)
    notes: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    flagged_for_adjudication: bool = False
    started_at: datetime | None = None
    submitted_at: datetime | None = None
    duration_seconds: float | None = Field(default=None, ge=0.0)

    @model_validator(mode="after")
    def _temporal_ordering(self) -> Annotation:
        if (
            self.started_at is not None
            and self.submitted_at is not None
            and self.submitted_at < self.started_at
        ):
            raise ValueError("submitted_at cannot be earlier than started_at")
        return self
