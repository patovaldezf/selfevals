"""GraderCard: versioned metadata about a grader (deterministic or judge).

A grader is "what scores a case." A GraderCard is its passport: what it
measures, how it's calibrated against humans, what thresholds gate its use.

Contract enforced here:
- If `blocking=True`, calibration thresholds must meet the canonical bar:
  precision >= 0.90, recall >= 0.95, high_risk_false_negatives == 0.
- Lifecycle: a card moves through CALIBRATING -> CALIBRATED -> IN_USE,
  may drift, and ends at RETIRED. State transitions are not validated as
  a strict machine here (we use the same loose enum semantics as canon
  §13 / operational §D.2 — they can be set freely until the calibration
  pipeline exists).
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import Field, model_validator

from bootstrap.schemas._base import BaseEntity, BootstrapModel, NonEmptyStr
from bootstrap.schemas.enums import GraderCardState, GroundTruthMethod


class GraderIO(BootstrapModel):
    """Inputs the grader inspects and the output schema it emits."""

    input_fields: list[NonEmptyStr] = Field(min_length=1)
    output_label_set: list[NonEmptyStr] = Field(default_factory=list)
    """For classification graders. Empty for scalar / pairwise graders."""

    output_kind: NonEmptyStr = "label"
    """label | score | pair_preference | structured."""


class HumanReference(BootstrapModel):
    """Pointer to the human-labeled set used to calibrate this grader."""

    dataset_id: NonEmptyStr | None = None
    annotator_count: int = Field(default=0, ge=0)
    adjudication: NonEmptyStr | None = None
    """e.g. 'majority_vote', 'expert_review', 'pairwise_resolution'."""


class CalibrationMetrics(BootstrapModel):
    precision: float | None = Field(default=None, ge=0.0, le=1.0)
    recall: float | None = Field(default=None, ge=0.0, le=1.0)
    f1: float | None = Field(default=None, ge=0.0, le=1.0)
    macro_f1: float | None = Field(default=None, ge=0.0, le=1.0)
    spearman: float | None = Field(default=None, ge=-1.0, le=1.0)
    mae: float | None = Field(default=None, ge=0.0)
    pairwise_agreement: float | None = Field(default=None, ge=0.0, le=1.0)
    high_risk_false_negatives: int | None = Field(default=None, ge=0)
    human_human_agreement: float | None = Field(default=None, ge=0.0, le=1.0)


class CalibrationThresholds(BootstrapModel):
    min_precision: float | None = Field(default=None, ge=0.0, le=1.0)
    min_recall: float | None = Field(default=None, ge=0.0, le=1.0)
    min_f1: float | None = Field(default=None, ge=0.0, le=1.0)
    max_high_risk_false_negatives: int | None = Field(default=None, ge=0)


class DegradeBehavior(BootstrapModel):
    """How a blocking grader degrades when calibration drops below thresholds.

    Operational §C.2: default is degrade_to_advisory + alert.
    """

    on_threshold_breach: NonEmptyStr = "degrade_to_advisory"
    """degrade_to_advisory | auto_disable | page_human."""

    alert_channels: list[NonEmptyStr] = Field(default_factory=list)


class GraderCard(BaseEntity):
    _id_prefix: ClassVar[str] = "gc"

    name: NonEmptyStr
    purpose: NonEmptyStr
    grader_kind: NonEmptyStr
    """deterministic | llm_judge | hybrid | human. Free string at schema
    level — runtime will dispatch on this."""

    method: GroundTruthMethod
    blocking: bool = False
    io: GraderIO
    human_reference: HumanReference = Field(default_factory=HumanReference)
    metrics: CalibrationMetrics = Field(default_factory=CalibrationMetrics)
    thresholds: CalibrationThresholds = Field(default_factory=CalibrationThresholds)
    review_cadence: NonEmptyStr = "monthly"
    degrade_behavior: DegradeBehavior = Field(default_factory=DegradeBehavior)
    state: GraderCardState = GraderCardState.CALIBRATING

    @model_validator(mode="after")
    def _blocking_requires_thresholds(self) -> GraderCard:
        if not self.blocking:
            return self
        t = self.thresholds
        bad: list[str] = []
        if t.min_precision is None or t.min_precision < 0.90:
            bad.append("min_precision >= 0.90")
        if t.min_recall is None or t.min_recall < 0.95:
            bad.append("min_recall >= 0.95")
        if t.max_high_risk_false_negatives is None or t.max_high_risk_false_negatives != 0:
            bad.append("max_high_risk_false_negatives == 0")
        if bad:
            raise ValueError(
                "blocking GraderCard must declare strict calibration thresholds: " + ", ".join(bad)
            )
        return self
