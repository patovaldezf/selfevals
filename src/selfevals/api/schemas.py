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
    storage_url: str | None = None
    storage_backend: str = "sqlite"


class MetricsWindow(BaseModel):
    start: datetime | None = None
    end: datetime | None = None


class PassRateMetricRow(BaseModel):
    grader: str
    label: str
    count: int
    rate: float


class PassRateMetricsResponse(BaseModel):
    workspace_id: str
    window: MetricsWindow
    experiment_id: str | None = None
    total: int
    items: list[PassRateMetricRow] = Field(default_factory=list)


class FailureModeMetricRow(BaseModel):
    failure_mode: str
    count: int
    rate: float


class FailureModeMetricsResponse(BaseModel):
    workspace_id: str
    window: MetricsWindow
    experiment_id: str | None = None
    total: int
    items: list[FailureModeMetricRow] = Field(default_factory=list)


class FailureClusterExample(BaseModel):
    """A concrete failing trace inside a cluster — enough to drill straight into
    the trace viewer (`/traces/{run_id}` resolves both `run_…` and `tr_…` ids)."""

    run_id: str
    experiment_id: str | None = None


class FailureClusterRow(BaseModel):
    """One cluster = one failure mode (§J.6 v1: cluster ≡ taxonomy mode).

    `failure_mode` is the stable slug carried on every grade; `title`/`status`
    are enriched from the workspace taxonomy when the mode is registered there
    (`status="unknown"` for a mode seen on a grade but not yet in the taxonomy —
    a candidate the analysis loop hasn't formalized). `examples` are capped
    sample traces for drill-down, not the full membership."""

    failure_mode: str
    failure_mode_id: str | None = None
    title: str | None = None
    status: str = "unknown"
    count: int
    rate: float
    examples: list[FailureClusterExample] = Field(default_factory=list)


class FailureClustersResponse(BaseModel):
    """Failing traces grouped by failure mode, ranked by frequency. Same window
    envelope as the other metrics so the FE filters identically."""

    workspace_id: str
    window: MetricsWindow
    experiment_id: str | None = None
    total: int
    items: list[FailureClusterRow] = Field(default_factory=list)


class ToolMetricRow(BaseModel):
    tool_name: str
    status: str
    count: int
    error_count: int
    avg_duration_ms: float | None = None
    retry_count: int = 0


class ToolMetricsResponse(BaseModel):
    workspace_id: str
    window: MetricsWindow
    experiment_id: str | None = None
    total: int
    items: list[ToolMetricRow] = Field(default_factory=list)


class CostMetricRow(BaseModel):
    provider: str
    model: str
    call_count: int
    total_cost_usd: float
    avg_cost_usd: float


class CostMetricsResponse(BaseModel):
    workspace_id: str
    window: MetricsWindow
    experiment_id: str | None = None
    total: int
    items: list[CostMetricRow] = Field(default_factory=list)


class TokenMetricRow(BaseModel):
    provider: str
    model: str
    call_count: int
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    total_tokens: int


class TokenMetricsResponse(BaseModel):
    workspace_id: str
    window: MetricsWindow
    experiment_id: str | None = None
    total: int
    items: list[TokenMetricRow] = Field(default_factory=list)


class LatencyMetricRow(BaseModel):
    metric: str
    count: int
    p50_ms: float | None = None
    p95_ms: float | None = None
    p99_ms: float | None = None


class LatencyMetricsResponse(BaseModel):
    workspace_id: str
    window: MetricsWindow
    experiment_id: str | None = None
    total: int
    items: list[LatencyMetricRow] = Field(default_factory=list)


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
    """202 acknowledgement: the run is queued for execution.

    The FE polls `GET .../experiments/{experiment_id}` (state climbs
    queued → running → completed/aborted) to follow progress. `run_id` is
    null here — trace run ids are minted per repetition inside the loop and
    surface on the iterations once they exist.

    `dispatch` tells the caller how the run executes: `redis-worker` means it
    was enqueued and needs a live `selfevals worker runs` to make progress;
    `in-process-thread` means the API is running it on a local daemon thread
    (no worker required).
    """

    experiment_id: str
    workspace_id: str
    state: str
    run_id: str | None = None
    job_id: str | None = None
    dispatch: str = "in-process-thread"


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


# --- Pairwise verdicts (LLM + human, for RLHF / judge calibration) ------
# HTTP envelopes around `runner/pairwise_ops.py`. A verdict can be emitted by an
# LLM judge or a human (via the web UI); both land here so the same pairs can be
# compared for calibration.


class PairRefBody(BaseModel):
    """One side of a compared pair. Mirrors `schemas.pairwise_verdict.PairRef`."""

    kind: str
    trace_id: str | None = None
    case_id: str | None = None
    iteration_id: str | None = None
    content_snapshot: str | None = None


class IngestPairwiseVerdict(BaseModel):
    """A single verdict to ingest. `id`/`workspace_id` are assigned server-side."""

    a_ref: PairRefBody
    b_ref: PairRefBody
    preferred: str
    margin: float = 0.0
    rationale: str | None = None
    judge_kind: str
    judge_id: str
    judge_model: str | None = None
    rubric_version: int | None = None
    position: str | None = None
    case_id: str | None = None
    dataset_id: str | None = None


class IngestPairwiseRequest(BaseModel):
    verdicts: list[IngestPairwiseVerdict] = Field(default_factory=list)


class PairwiseVerdictResponse(BaseModel):
    id: str
    a_ref: PairRefBody
    b_ref: PairRefBody
    preferred: str
    margin: float
    rationale: str | None = None
    judge_kind: str
    judge_id: str
    rubric_version: int | None = None
    experiment_id: str | None = None
    case_id: str | None = None
    created_at: datetime


class PairwiseIngestSummaryResponse(BaseModel):
    ingested: int


class PairwiseCalibrationCellResponse(BaseModel):
    rubric_version: int | None = None
    compared_pairs: int
    agreements: int
    disagreements: int
    agreement_rate: float


class PairwiseCalibrationResponse(BaseModel):
    compared_pairs: int
    agreements: int
    disagreements: int
    agreement_rate: float
    by_rubric_version: list[PairwiseCalibrationCellResponse] = Field(default_factory=list)


# --- Pairwise tournaments (rank N candidates via Elo / Bradley-Terry) ---


class TournamentCandidateBody(BaseModel):
    """One entrant: a stable id, the text to judge, and an optional trace link."""

    id: str
    output_text: str
    trace_id: str | None = None


class RunTournamentRequest(BaseModel):
    candidates: list[TournamentCandidateBody] = Field(default_factory=list)
    judge_entrypoint: str
    """Dotted path `mod:fn` to the LLM judge callable (same shape as a grader's
    `judge_entrypoint`)."""
    rubric: str
    strategy: str = "all_pairs"
    method: str = "elo"
    baseline_id: str | None = None
    comparisons_per_candidate: int = 3
    swiss_rounds: int = 3
    swap_and_average: bool = False
    case_input: dict[str, Any] | None = None


class RankingRowResponse(BaseModel):
    candidate_id: str
    rank: int
    score: float
    wins: int
    losses: int
    ties: int
    n_comparisons: int


class TournamentResponse(BaseModel):
    id: str
    experiment_id: str | None = None
    strategy: str
    method: str
    candidate_ids: list[str]
    baseline_id: str | None = None
    n_comparisons: int
    swap_and_average: bool
    ranking: list[RankingRowResponse] = Field(default_factory=list)
    created_at: datetime


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


class AppendDatasetCaseRequest(BaseModel):
    """Append one validated EvalCase dict to a dataset.

    Frozen regression datasets are immutable. When `create_version_if_frozen`
    is true, the backend creates a new active dataset version instead of
    mutating the frozen manifest.
    """

    case: dict[str, Any]
    create_version_if_frozen: bool = True


class AppendDatasetCaseResponse(BaseModel):
    dataset: DatasetDetailResponse
    case_id: str
    created_new_dataset: bool = False


# --- Failure-mode taxonomy (loop-closer) --------------------------------
# View + request shapes for the taxonomy UI. The domain logic lives in
# `cli/analyze_commands.py`; these expose it over HTTP. A mode's status is the
# promotion gate (candidate → official → retired); `example_count` keeps the
# list cheap (no full example bodies until the detail view needs them).


class FailureModeResponse(BaseModel):
    """One failure mode, projected for the taxonomy UI."""

    id: str
    slug: str
    title: str
    definition: str
    status: str
    parent_mode_id: str | None = None
    proposed_by: str
    example_count: int = 0
    first_seen_iteration: int | None = None
    superseded_by: str | None = None
    created_at: datetime
    updated_at: datetime


class FailureModeListResponse(BaseModel):
    items: list[FailureModeResponse] = Field(default_factory=list)


class MergeFailureModeRequest(BaseModel):
    """Merge this mode's examples into `into_id`, then retire the source."""

    into_id: str = Field(min_length=1)


class EditFailureModeRequest(BaseModel):
    """Patch a mode's human-facing text. At least one field must be set."""

    title: str | None = None
    definition: str | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> EditFailureModeRequest:
        if self.title is None and self.definition is None:
            raise ValueError("provide at least one of `title` or `definition`")
        return self


# --- Baseline & regression (loop-closer) --------------------------------
# Exposes `runner/baseline.py` + `ci/regression.py` over HTTP, anchored to a
# dataset. The FE shows the current baseline on the dataset/iteration views and
# runs a regression check against it.


class BaselineResponse(BaseModel):
    dataset_id: str
    iteration_id: str
    experiment_id: str | None = None
    primary_metric_name: str
    primary_metric_value: float
    error_rate: float | None = None
    created_at: datetime


class SetBaselineRequest(BaseModel):
    """Re-anchor a dataset's baseline. `iteration_id` omitted = use the best
    completed iteration on the dataset (same default as the CLI)."""

    iteration_id: str | None = None


class RegressionCheckRequest(BaseModel):
    iteration_id: str = Field(min_length=1)
    primary_drop: float = 0.0
    per_class_f1_drop: float = 0.05
    error_rate_rise: float = 0.0


class RegressionFindingResponse(BaseModel):
    """Mirrors `ci.regression.RegressionFinding`. `regressed=True` means this
    signal failed the gate; a populated `detail` with `regressed=False` is an
    informational note (improvement / class appeared)."""

    signal: str
    baseline: float | None = None
    current: float | None = None
    delta: float | None = None
    regressed: bool
    detail: str


class RegressionResultResponse(BaseModel):
    dataset_id: str
    iteration_id: str
    regressed: bool
    findings: list[RegressionFindingResponse] = Field(default_factory=list)


# --- Error-analysis bundle / ingest (loop-closer) -----------------------
# Thin HTTP envelopes around `analysis/bundle.py` + `analysis/ingest.py`. The
# bundle/result bodies themselves are the domain Pydantic models from
# `analysis/schemas.py`, passed through as opaque JSON so the contract stays
# defined in one place.


class AnalysisIngestSummaryResponse(BaseModel):
    assignments_applied: int
    created_candidates: list[str] = Field(default_factory=list)
    updated_candidates: list[str] = Field(default_factory=list)
    hypotheses_recorded: int
