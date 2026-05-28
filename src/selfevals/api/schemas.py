"""Pydantic response models for the HTTP bridge.

These are *view* shapes — denormalized snapshots of the canonical
entities that the web UI needs. The canonical schemas in
`selfevals.schemas` stay the source of truth; this module simply
chooses what to expose and in what shape.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


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


class ThreadTurn(BaseModel):
    """One trace within a thread, projected as a turn for the thread view."""

    trace_id: str
    run_id: str
    position: int
    experiment_id: str | None = None
    iteration: int | None = None
    final_state: str
    started_at: datetime
    ended_at: datetime | None = None
    primary_grade: str | None = None
    grader_results: list[dict[str, Any]] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)


class ThreadResponse(BaseModel):
    """All traces sharing a thread_id, assembled into an ordered conversation."""

    thread_id: str
    turn_count: int
    turns: list[ThreadTurn] = Field(default_factory=list)


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
