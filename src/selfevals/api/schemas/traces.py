"""Trace, case, and per-scenario (expected/detected/matched) response shapes."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SpanSummary(BaseModel):
    id: str
    parent_id: str | None
    kind: str
    name: str
    started_at: datetime
    duration_ms: int
    detail: dict[str, Any] = Field(default_factory=dict)


class TraceResponse(BaseModel):
    id: str
    run_id: str
    experiment_id: str | None
    experiment_name: str | None = None
    iteration: int | None
    thread_id: str | None = None
    thread_position: int | None = None
    final_state: str
    started_at: datetime
    ended_at: datetime | None
    spans: list[SpanSummary]
    metrics: dict[str, Any]


class FeatureRef(BaseModel):
    """A case's feature classification: one primary path + optional secondaries.

    Mirrors `schemas.eval_case.FeatureTag`. Exposed as an object (not a flattened
    string) so the OpenAPI contract matches what the API actually serializes — the
    prior `str(taxonomy.feature)` leaked a Pydantic repr that the type claimed was
    a plain string."""

    primary: str
    secondary: list[str] = Field(default_factory=list)


class CaseSummary(BaseModel):
    """An eval case as persisted under an experiment, shaped for the cases list.

    Surfaces the navigable identity (`id`, `name`, `input`) plus the facets the
    UI needs to make sense of the set: graders applied, the taxonomy target,
    and `holdout` (so reserved cases are flagged, not hidden). `input` is the
    raw payload fed to the agent — the FE renders it (and detects conversations
    via a `messages` key) without a second round-trip.
    """

    id: str
    name: str
    task_type: str
    modalities: list[str] = Field(default_factory=list)
    input: dict[str, Any] = Field(default_factory=dict)
    graders: list[str] = Field(default_factory=list)
    holdout: bool = False
    is_conversation: bool = False
    feature: FeatureRef | None = None
    level: str | None = None
    dataset_type: str | None = None
    latest_run_id: str | None = None
    """run_id of this case's most recent persisted trace in the experiment, so
    the FE can link case → trace inline. None when no trace was persisted for
    this case (e.g. it passed under `persist_traces="failed"`)."""
    latest_trace_id: str | None = None
    """Entity id (`tr_...`) of that same most-recent trace. Either id resolves
    via `GET .../traces/{id}`."""


class CaseListResponse(BaseModel):
    """The full case set of an experiment, holdout cases included and flagged.

    `holdout_count` lets the FE show "N cases (M held out)" without re-counting.
    """

    cases: list[CaseSummary] = Field(default_factory=list)
    total: int
    holdout_count: int


class ExpectedView(BaseModel):
    """What the case declared it expected — only the dimensions it actually
    declares are populated. A classification case carries `structured_output`; a
    substring case carries `must_include`; a tool case carries `required_tools`.
    Unused dimensions are omitted from the JSON (serialized with
    `exclude_none`/empty-skipped), so the payload stays compact at scale instead
    of carrying nulls for every possible rule."""

    model_config = ConfigDict(extra="forbid")

    structured_output: dict[str, Any] | None = None
    must_include: list[str] | None = None
    must_not_include: list[str] | None = None
    required_tools: list[str] | None = None
    forbidden_tools: list[str] | None = None


class DetectedView(BaseModel):
    """What the agent actually produced, projected to mirror the declared
    `ExpectedView` so the FE can render a direct expected-vs-detected diff.

    `content` is the classified message (the agent's reply). `structured_output`
    is its structured payload. `missing`/`forbidden_present` name the specific
    substrings that broke a `must_include`/`must_not_include` rule.
    `tools_invoked` lists the tools the run actually called. Like `ExpectedView`,
    only relevant keys are emitted."""

    model_config = ConfigDict(extra="forbid")

    content: str | None = None
    structured_output: dict[str, Any] | None = None
    missing: list[str] | None = None
    forbidden_present: list[str] | None = None
    tools_invoked: list[str] | None = None


class ScenarioResult(BaseModel):
    """One evaluated scenario — a case, or one turn of a conversation case.

    The single recursive shape used everywhere the FE needs "expected vs detected
    vs matched": `/experiments/{id}/results` (per case) and `/threads/{id}` (per
    turn). A conversation case carries its turns in `turns`, each a `ScenarioResult`
    of the same shape, so the FE renders identically at any depth.

    `expected`/`detected` are derived per declared dimension (see `ExpectedView`),
    not fixed blobs — they're `None` when the case declares nothing to compare.
    `message` is the classified reply text, always present when the trace has one."""

    case_id: str
    case_name: str | None = None
    run_id: str | None = None
    trace_id: str | None = None
    iteration: int
    position: int | None = None
    """0-based turn index within a conversation; None for a top-level case."""
    matched: bool | None = None
    """Whether the primary grade passed. None when there's no persisted trace."""
    score: float | None = None
    label: str | None = None
    started_at: datetime | None = None
    """When this turn's trace started. Poblado por turno de thread; `None` para
    cases sin trace persistido o single-shot sin timestamp."""
    message: str | None = None
    """The classified message — the agent's reply text for this case/turn."""
    failure_modes: list[str] = Field(default_factory=list)
    expected: ExpectedView | None = None
    detected: DetectedView | None = None
    grader_results: list[dict[str, Any]] = Field(default_factory=list)
    turns: list[ScenarioResult] = Field(default_factory=list)
    """Per-turn breakdown for a conversation case; empty for single-shot cases or
    when turn expansion wasn't requested (`?include=turns`)."""


ScenarioResult.model_rebuild()  # resolve the recursive `turns` forward ref


class ExperimentResultsResponse(BaseModel):
    """Per-scenario results for an experiment's best iteration.

    Cases with no persisted trace are still listed (`expected` from the spec,
    `detected`/`matched=None`) so the set is honest — under `persist_traces`
    other than `"all"`, passing cases have no trace to show."""

    experiment_id: str
    iteration: int | None = None
    cases: list[ScenarioResult] = Field(default_factory=list)
    total: int


class ThreadResponse(BaseModel):
    """All traces sharing a thread_id, assembled into an ordered conversation.

    Each turn is a `ScenarioResult` — the same shape `/results` uses — so the FE
    renders a turn and a case identically, with per-turn expected/detected/matched
    and the classified `message`. (Replaces the old `ThreadTurn`.)"""

    thread_id: str
    turn_count: int
    turns: list[ScenarioResult] = Field(default_factory=list)


class PromoteCaseDraftRequest(BaseModel):
    """Optional edits when drafting a regression case from a trace."""

    name: str | None = None
    notes: str | None = None


class PromoteCaseDraftResponse(BaseModel):
    """Human-reviewable EvalCase draft built from a persisted trace."""

    case: dict[str, Any]
    source_trace_id: str
    source_run_id: str
    source_case_id: str
    warnings: list[str] = Field(default_factory=list)
