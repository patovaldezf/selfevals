"""Wire schemas for the error-analysis handshake.

These are the contract between selfevals and an external coding agent (the
`error-analysis` skill). selfevals emits an `AnalysisBundle` (pull) and ingests
an `AnalysisResult` (push). Get these right and any agent can honour the
protocol. See docs/spec/error_analysis_design.md §4.

They are plain `SelfEvalsModel`s (not entities) — transport shapes, not stored
rows. The persistence happens by translating an `AnalysisResult` into
`FailureMode` entities and `GraderResult` updates in `ingest.py`.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from selfevals.schemas._base import NonEmptyStr, SelfEvalsModel

ANALYSIS_SCHEMA_VERSION = "1.0.0"


class TaxonomyEntry(SelfEvalsModel):
    """A live failure mode the agent must classify AGAINST (never rename)."""

    id: NonEmptyStr
    slug: NonEmptyStr
    title: str
    definition: str
    status: str


class BundleGrade(SelfEvalsModel):
    label: str
    score: float | None = None
    deterministic_modes: list[str] = Field(default_factory=list)
    judge_reason: str | None = None


class BundleMessage(SelfEvalsModel):
    role: str
    content: str


class BundleErrorSpan(SelfEvalsModel):
    kind: str
    name: str
    error: str | None = None


class BundleTrace(SelfEvalsModel):
    """One failed trace the agent needs to code."""

    trace_id: NonEmptyStr
    run_id: NonEmptyStr
    thread_id: str | None = None
    eval_case_id: str | None = None
    grade: BundleGrade
    transcript: list[BundleMessage] = Field(default_factory=list)
    first_error_span: BundleErrorSpan | None = None


class AnalysisBundle(SelfEvalsModel):
    schema_version: str = ANALYSIS_SCHEMA_VERSION
    workspace_id: NonEmptyStr
    experiment_id: NonEmptyStr
    iteration: int | None = None
    taxonomy: list[TaxonomyEntry] = Field(default_factory=list)
    traces: list[BundleTrace] = Field(default_factory=list)
    instructions_ref: str = "skill://error-analysis"


class Assignment(SelfEvalsModel):
    """Trace → failure mode. Either an existing `mode_id` (classify) XOR a
    `new_mode_slug` (propose). Never both, never neither — enforced here and
    again transactionally in ingest."""

    trace_id: NonEmptyStr
    mode_id: str | None = None
    new_mode_slug: str | None = None
    open_note: str | None = None
    quote: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _exactly_one_target(self) -> Assignment:
        has_id = self.mode_id is not None
        has_slug = self.new_mode_slug is not None
        if has_id == has_slug:
            raise ValueError(
                "assignment must set exactly one of mode_id (classify) or "
                "new_mode_slug (propose) — never both, never neither"
            )
        return self


class ProposedMode(SelfEvalsModel):
    """A new candidate mode discovered during axial coding."""

    slug: NonEmptyStr
    title: NonEmptyStr
    definition: NonEmptyStr
    parent_slug: str | None = None


class Hypothesis(SelfEvalsModel):
    """A testable change targeting a mode, fed to the proposer (not auto-run)."""

    targets_mode_slug: NonEmptyStr
    statement: NonEmptyStr
    suggested_parameters: dict[str, Any] = Field(default_factory=dict)


class AnalysisResult(SelfEvalsModel):
    schema_version: str = ANALYSIS_SCHEMA_VERSION
    assignments: list[Assignment] = Field(default_factory=list)
    proposed_modes: list[ProposedMode] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
