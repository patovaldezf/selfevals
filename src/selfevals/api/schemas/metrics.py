"""Aggregate metrics response shapes: pass-rate, failure-modes, tools, cost, tokens, latency."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


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
