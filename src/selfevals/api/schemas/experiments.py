"""Experiment lifecycle, iteration, and results response/request shapes."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


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
    `dispatch-pending` means the durable job exists but Redis dispatch failed,
    so a run worker must recover it from Postgres; `in-process-thread` means the
    API is running it on a local daemon thread (no worker required).
    """

    experiment_id: str
    workspace_id: str
    state: str
    run_id: str | None = None
    job_id: str | None = None
    dispatch: str = "in-process-thread"
