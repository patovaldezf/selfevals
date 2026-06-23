"""Workspace metrics queries for production agent monitoring.

Postgres-backed installs use normalized trace fact tables. The storage backend
exposes typed metric aggregation methods that we call directly.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from selfevals.api.schemas import (
    CostMetricRow,
    CostMetricsResponse,
    FailureModeMetricRow,
    FailureModeMetricsResponse,
    LatencyMetricRow,
    LatencyMetricsResponse,
    MetricsWindow,
    PassRateMetricRow,
    PassRateMetricsResponse,
    TokenMetricRow,
    TokenMetricsResponse,
    ToolMetricRow,
    ToolMetricsResponse,
)
from selfevals.storage.interface import StorageInterface


def pass_rate_metrics(
    storage: StorageInterface,
    *,
    workspace_id: str,
    start: datetime | None = None,
    end: datetime | None = None,
    experiment_id: str | None = None,
    grader: str | None = None,
) -> PassRateMetricsResponse:
    rows = storage.pass_rate_metrics(
        workspace_id=workspace_id,
        start=start,
        end=end,
        experiment_id=experiment_id,
        grader=grader,
    )
    total = sum(int(row["count"]) for row in rows)
    return PassRateMetricsResponse(
        workspace_id=workspace_id,
        window=MetricsWindow(start=start, end=end),
        experiment_id=experiment_id,
        total=total,
        items=[
            PassRateMetricRow(
                grader=str(row["grader"]),
                label=str(row["label"]),
                count=int(row["count"]),
                rate=_rate(int(row["count"]), total),
            )
            for row in rows
        ],
    )


def failure_mode_metrics(
    storage: StorageInterface,
    *,
    workspace_id: str,
    start: datetime | None = None,
    end: datetime | None = None,
    experiment_id: str | None = None,
    grader: str | None = None,
) -> FailureModeMetricsResponse:
    rows = storage.failure_mode_metrics(
        workspace_id=workspace_id,
        start=start,
        end=end,
        experiment_id=experiment_id,
        grader=grader,
    )
    total = sum(int(row["count"]) for row in rows)
    return FailureModeMetricsResponse(
        workspace_id=workspace_id,
        window=MetricsWindow(start=start, end=end),
        experiment_id=experiment_id,
        total=total,
        items=[
            FailureModeMetricRow(
                failure_mode=str(row["failure_mode"]),
                count=int(row["count"]),
                rate=_rate(int(row["count"]), total),
            )
            for row in rows
        ],
    )


def tool_metrics(
    storage: StorageInterface,
    *,
    workspace_id: str,
    start: datetime | None = None,
    end: datetime | None = None,
    experiment_id: str | None = None,
    tool_name: str | None = None,
) -> ToolMetricsResponse:
    rows = storage.tool_metrics(
        workspace_id=workspace_id,
        start=start,
        end=end,
        experiment_id=experiment_id,
        tool_name=tool_name,
    )
    total = sum(int(row["count"]) for row in rows)
    return ToolMetricsResponse(
        workspace_id=workspace_id,
        window=MetricsWindow(start=start, end=end),
        experiment_id=experiment_id,
        total=total,
        items=[
            ToolMetricRow(
                tool_name=str(row["tool_name"]),
                status=str(row["status"]),
                count=int(row["count"]),
                error_count=int(row["error_count"]),
                avg_duration_ms=(
                    float(row["avg_duration_ms"]) if row["avg_duration_ms"] is not None else None
                ),
                retry_count=int(row["retry_count"]),
            )
            for row in rows
        ],
    )


def cost_metrics(
    storage: StorageInterface,
    *,
    workspace_id: str,
    start: datetime | None = None,
    end: datetime | None = None,
    experiment_id: str | None = None,
    model: str | None = None,
) -> CostMetricsResponse:
    rows = storage.cost_metrics(
        workspace_id=workspace_id,
        start=start,
        end=end,
        experiment_id=experiment_id,
        model=model,
    )
    total = sum(int(row["call_count"]) for row in rows)
    return CostMetricsResponse(
        workspace_id=workspace_id,
        window=MetricsWindow(start=start, end=end),
        experiment_id=experiment_id,
        total=total,
        items=[
            CostMetricRow(
                provider=str(row["provider"]),
                model=str(row["model"]),
                call_count=int(row["call_count"]),
                total_cost_usd=float(row["total_cost_usd"]),
                avg_cost_usd=float(row["avg_cost_usd"]),
            )
            for row in rows
        ],
    )


def token_metrics(
    storage: StorageInterface,
    *,
    workspace_id: str,
    start: datetime | None = None,
    end: datetime | None = None,
    experiment_id: str | None = None,
    model: str | None = None,
) -> TokenMetricsResponse:
    rows = storage.token_metrics(
        workspace_id=workspace_id,
        start=start,
        end=end,
        experiment_id=experiment_id,
        model=model,
    )
    total = sum(int(row["call_count"]) for row in rows)
    return TokenMetricsResponse(
        workspace_id=workspace_id,
        window=MetricsWindow(start=start, end=end),
        experiment_id=experiment_id,
        total=total,
        items=[
            TokenMetricRow(
                provider=str(row["provider"]),
                model=str(row["model"]),
                call_count=int(row["call_count"]),
                input_tokens=int(row["input_tokens"]),
                output_tokens=int(row["output_tokens"]),
                reasoning_tokens=int(row["reasoning_tokens"]),
                total_tokens=int(row["total_tokens"]),
            )
            for row in rows
        ],
    )


def latency_metrics(
    storage: StorageInterface,
    *,
    workspace_id: str,
    start: datetime | None = None,
    end: datetime | None = None,
    experiment_id: str | None = None,
) -> LatencyMetricsResponse:
    rows = storage.latency_metrics(
        workspace_id=workspace_id,
        start=start,
        end=end,
        experiment_id=experiment_id,
    )
    total = sum(int(row["count"]) for row in rows)
    return LatencyMetricsResponse(
        workspace_id=workspace_id,
        window=MetricsWindow(start=start, end=end),
        experiment_id=experiment_id,
        total=total,
        items=[
            LatencyMetricRow(
                metric=str(row["metric"]),
                count=int(row["count"]),
                p50_ms=_optional_float(row["p50_ms"]),
                p95_ms=_optional_float(row["p95_ms"]),
                p99_ms=_optional_float(row["p99_ms"]),
            )
            for row in rows
        ],
    )


def _rate(count: int, total: int) -> float:
    return count / total if total else 0.0


def _optional_float(value: Any) -> float | None:
    return float(value) if value is not None else None
