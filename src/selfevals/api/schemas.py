"""Pydantic response models for the HTTP bridge.

These are *view* shapes — denormalized snapshots of the canonical
entities that the web UI needs. The canonical schemas in
`selfevals.schemas` stay the source of truth; this module simply
chooses what to expose and in what shape.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class HealthResponse(BaseModel):
    status: str
    db_path: str


class WorkspaceSummary(BaseModel):
    id: str
    slug: str
    name: str
    description: str | None = None
    owner_id: str | None = None
    created_at: datetime
    experiment_count: int = 0
    last_run_at: datetime | None = None


class WorkspaceListResponse(BaseModel):
    workspaces: list[WorkspaceSummary]


class WorkspaceResponse(BaseModel):
    id: str
    slug: str
    name: str
    description: str | None = None
    owner_id: str | None = None
    created_at: datetime
    experiment_count: int
    recent_health: float | None = Field(
        default=None,
        description="Fraction of recent experiments that landed on keep_candidate.",
    )


class CreateWorkspaceRequest(BaseModel):
    slug: str = Field(min_length=1, max_length=63)
    name: str | None = None
    description: str | None = None


class IterationSummary(BaseModel):
    id: str
    iteration: int
    state: str
    hypothesis: str
    proposed_parameters: dict[str, Any] = Field(default_factory=dict)
    primary_metric_name: str | None = None
    primary_metric_value: float | None = None
    delta_vs_best: float | None = None
    decision_outcome: str | None = None
    decision_rationale: str | None = None
    cost_usd: float | None = None
    duration_seconds: float | None = None
    trace_run_ids: list[str] = Field(default_factory=list)
    created_at: datetime


class IterationListResponse(BaseModel):
    iterations: list[IterationSummary]


class ExperimentSummary(BaseModel):
    id: str
    name: str
    goal: str
    mode: str
    state: str
    primary_metric: str
    primary_target: dict[str, Any]
    proposer_strategy: str
    max_iterations: int
    created_at: datetime
    updated_at: datetime
    iteration_count: int = 0


class ExperimentListPage(BaseModel):
    """Paginated `GET /workspaces/{ws}/experiments` (A8).

    Schema-versioned envelope so the FE can offer "load more" /
    cursor-style navigation when an installation accumulates many
    experiments. Ships before any FE pagination UI lands so the
    contract is stable in V0 — the day a user has 500+ experiments
    we don't need a breaking API change to surface a pager.
    """

    items: list[ExperimentSummary]
    total: int
    limit: int
    offset: int
    has_more: bool


class ExperimentDetailResponse(BaseModel):
    """Shape returned by GET /workspaces/{ws}/experiments/{id}.

    `result` is the JSON shape from `selfevals.reporter.render_json`
    when there is at least one completed iteration to reconstruct;
    `None` when the experiment has not run yet.
    """

    summary: ExperimentSummary
    result: dict[str, Any] | None = None
    iterations: list[IterationSummary] = Field(default_factory=list)
    best_iteration: dict[str, Any] | None = Field(
        default=None,
        description=(
            "The winning iteration (highest primary metric), lifted from "
            "`result.best_iteration` to a first-class field so the FE need not "
            "dig into the report JSON. Same per-iteration shape as the reporter "
            "(`render_json`); `null` when the experiment has no iterations."
        ),
    )


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


class FunnelNodeResponse(BaseModel):
    """One rolled-up funnel node (recursive). Mirrors
    aggregator.FunnelNode.to_dict() — additive, never affects decisions."""

    key: str
    count: int
    mean_score: float | None = None
    total_weight: float = 0.0
    label_counts: dict[str, int] = Field(default_factory=dict)
    failure_mode_counts: dict[str, int] = Field(default_factory=dict)
    children: dict[str, FunnelNodeResponse] = Field(default_factory=dict)


class FunnelResponse(BaseModel):
    """Per-iteration grader funnel. `nodes` is empty when no grader
    emitted a breakdown (the common case for the pingpong example)."""

    iteration_id: str
    iteration: int
    nodes: dict[str, FunnelNodeResponse] = Field(default_factory=dict)


FunnelNodeResponse.model_rebuild()  # explicit: resolve the recursive forward ref


# --- Compare (B3) -------------------------------------------------------
#
# Pydantic mirrors of the frozen dataclasses in
# `selfevals.reporter.compare`. The reporter stays pydantic-free (it is
# core and must not depend on the web layer); the dataclass→pydantic
# projection happens in `selfevals.api.queries.load_compare`.


class CompareMetricRow(BaseModel):
    name: str
    a: float | None
    b: float | None
    delta: float | None


class CompareParamRow(BaseModel):
    key: str
    a: str
    b: str
    changed: bool


class CompareFunnelRow(BaseModel):
    path: str
    a: float | None
    b: float | None
    delta: float | None


class CompareFailureModes(BaseModel):
    only_a: dict[str, int] = Field(default_factory=dict)
    only_b: dict[str, int] = Field(default_factory=dict)
    common: dict[str, tuple[int, int]] = Field(default_factory=dict)


class CompareRecommendation(BaseModel):
    kind: str
    """One of: "winner" | "tie" | "different_metric" | "none"."""
    winner: str | None = None
    metric_name: str | None = None
    a_metric_name: str | None = None
    b_metric_name: str | None = None
    a_value: float | None = None
    b_value: float | None = None
    delta: float | None = None
    new_failure_modes: list[str] = Field(default_factory=list)


class CompareResponse(BaseModel):
    """Server-rendered structured diff of two IterationRecords.

    Single source of truth: the reporter's `compute_compare`. The web UI
    renders this directly instead of recomputing deltas client-side.
    """

    a_id: str
    b_id: str
    a_iteration: int
    b_iteration: int
    a_created_at: str
    b_created_at: str
    a_decision: str | None = None
    b_decision: str | None = None
    proposal_diff: list[CompareParamRow] = Field(default_factory=list)
    metrics_diff: list[CompareMetricRow] = Field(default_factory=list)
    failure_modes: CompareFailureModes
    funnel_diff: list[CompareFunnelRow] = Field(default_factory=list)
    recommendation: CompareRecommendation
    holdout_status: str = "unavailable"
    """Whether the diff is validated on a held-out split. `IterationRecord`
    carries no split classification, so this is honestly "unavailable"
    rather than a fabricated holdout number — the FE renders it as a
    first-class caveat, never a fake metric."""


class DecisionRecordResponse(BaseModel):
    """One decision in an experiment's history.

    The shape is flat and stable (unlike the full `DecisionRecord` dump),
    so it is worth typing for the Orval-generated client rather than
    leaking a raw `dict`.
    """

    id: str
    iteration: int
    outcome: str
    automated_rationale: str
    human_rationale: str | None = None
    metrics_snapshot: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class ActiveRun(BaseModel):
    """A trace run currently streaming spans through the broker."""

    workspace_id: str
    run_id: str


class ActiveRunsResponse(BaseModel):
    runs: list[ActiveRun] = Field(default_factory=list)


class RunExperimentRequest(BaseModel):
    """Launch an experiment over HTTP (F1).

    Provide exactly one source:

    * `spec_path` — a path/name of a YAML spec already on the server's disk.
    * `spec_inline` — the spec as a JSON object (same shape as the YAML).
      Inline specs must embed their cases via `dataset.cases_inline`; a
      `dataset.cases_path` has no on-disk base to resolve against and is
      rejected.

    The optional overrides mirror `selfevals run` flags.
    """

    spec_path: str | None = None
    spec_inline: dict[str, Any] | None = None
    dataset_id: str | None = None
    """Optional override: run against this persisted dataset instead of whatever
    the spec's `dataset:` block declares. Lets the FE pick a dataset at launch
    without editing the spec — the experiment consumes the dataset by reference."""
    max_iterations: int | None = Field(default=None, ge=1)
    reps: int | None = Field(default=None, ge=1)
    persist_traces: Literal["none", "all", "failed"] | None = None

    @model_validator(mode="after")
    def _exactly_one_source(self) -> RunExperimentRequest:
        if (self.spec_path is None) == (self.spec_inline is None):
            raise ValueError("provide exactly one of `spec_path` or `spec_inline`")
        return self


class RunExperimentResponse(BaseModel):
    """202 acknowledgement: the run is queued on a background thread.

    The FE polls `GET .../experiments/{experiment_id}` (state climbs
    queued → running → completed/aborted) to follow progress. `run_id` is
    null here — trace run ids are minted per repetition inside the loop and
    surface on the iterations once they exist.
    """

    experiment_id: str
    workspace_id: str
    state: str
    run_id: str | None = None


class SplitAllocationView(BaseModel):
    """A dataset's optimization/holdout/reliability split fractions."""

    optimization: float
    holdout: float
    reliability: float
    other: dict[str, float] = Field(default_factory=dict)


class DatasetStatisticsView(BaseModel):
    """Portfolio counts for a dataset's cases (computed at materialization)."""

    total_cases: int
    by_level: dict[str, int] = Field(default_factory=dict)
    by_feature: dict[str, int] = Field(default_factory=dict)
    by_source: dict[str, int] = Field(default_factory=dict)
    by_risk: dict[str, int] = Field(default_factory=dict)
    holdout_count: int = 0
    pii_breakdown: dict[str, int] = Field(default_factory=dict)


class DatasetSummary(BaseModel):
    """A dataset as it appears in a list — identity + shape, no case bodies."""

    id: str
    name: str
    description: str | None = None
    dataset_type: str
    status: str
    case_count: int
    manifest_hash: str | None = None
    created_at: datetime
    updated_at: datetime


class DatasetListPage(BaseModel):
    """Paginated `GET /workspaces/{ws}/datasets`."""

    items: list[DatasetSummary] = Field(default_factory=list)
    total: int
    limit: int
    offset: int
    has_more: bool


class DatasetDetailResponse(BaseModel):
    """One dataset with its split, statistics, and resolved case summaries.

    `cases` reuses `CaseSummary` — the same shape the experiment cases list
    returns — so the FE renders a dataset's cases with one component.
    """

    id: str
    name: str
    description: str | None = None
    dataset_type: str
    status: str
    case_count: int
    manifest_hash: str | None = None
    split_allocation: SplitAllocationView
    statistics: DatasetStatisticsView | None = None
    cases: list[CaseSummary] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class CreateDatasetRequest(BaseModel):
    """Create a dataset over HTTP (JSON body). Provide exactly one case source:

    * `cases` — the case dicts inline in the request body.
    * `cases_path` — a path to a JSONL file already on the server's disk.

    (To upload a `.jsonl` file directly, use the multipart `…/datasets/upload`
    endpoint instead.) `dataset_type` defaults to `capability`; an optional
    `split_allocation` overrides the default 0.7/0.2/0.1 portfolio.
    """

    name: str = Field(min_length=1)
    description: str | None = None
    dataset_type: str = "capability"
    cases: list[dict[str, Any]] | None = None
    cases_path: str | None = None
    split_allocation: dict[str, float] | None = None

    @model_validator(mode="after")
    def _exactly_one_source(self) -> CreateDatasetRequest:
        if (self.cases is None) == (self.cases_path is None):
            raise ValueError("provide exactly one of `cases` or `cases_path`")
        return self
