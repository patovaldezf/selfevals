"""Workspace metrics queries for production agent monitoring.

Postgres-backed installs use normalized trace fact tables. The storage backend
exposes typed metric aggregation methods that we call directly.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from selfevals.api.schemas import (
    CostMetricRow,
    CostMetricsResponse,
    FailureClusterExample,
    FailureClusterRow,
    FailureClustersResponse,
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
from selfevals.schemas.failure_mode import FailureMode
from selfevals.schemas.trace import Trace
from selfevals.storage.interface import ListFilter, StorageInterface


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


def failure_clusters(
    storage: StorageInterface,
    *,
    workspace_id: str,
    start: datetime | None = None,
    end: datetime | None = None,
    experiment_id: str | None = None,
    grader: str | None = None,
    limit: int | None = None,
    examples_per_cluster: int = 5,
) -> FailureClustersResponse:
    """Group failing traces by failure mode (§J.6 v1: cluster ≡ taxonomy mode).

    Same sweep as `failure_mode_metrics`, but also collects up to
    `examples_per_cluster` sample `run_id`s per mode for drill-down, and enriches
    each cluster with the mode's `title`/`status`/`id` from the workspace
    taxonomy. A mode seen on a grade but absent from the taxonomy stays
    `status="unknown"` — an un-formalized candidate, surfaced honestly rather
    than hidden.

    No Postgres hot path yet: the aggregated fact tables don't carry the example
    `run_id`s this view needs. The Trace-JSON fallback below is enough for the
    local quickstart and tests. TODO(J.6): add a hot method once the fact tables
    retain per-mode example run ids; consider semantic (LLM) clustering on top.
    """
    counts: Counter[str] = Counter()
    examples: dict[str, list[FailureClusterExample]] = defaultdict(list)
    for trace in _filtered_traces(storage, workspace_id, start, end, experiment_id):
        # One trace contributes a given mode at most once, so the example list
        # and the count agree on what "a member of this cluster" means.
        modes_here: set[str] = set()
        for result in trace.grader_results:
            if grader is not None and result.grader != grader:
                continue
            modes_here.update(result.failure_modes)
        for mode in modes_here:
            counts[mode] += 1
            bucket = examples[mode]
            if len(bucket) < examples_per_cluster:
                bucket.append(
                    FailureClusterExample(
                        run_id=trace.run.run_id,
                        experiment_id=trace.run.experiment_id,
                    )
                )

    taxonomy = _taxonomy_index(storage, workspace_id)
    ranked = counts.most_common(limit) if limit is not None else counts.most_common()
    total = sum(count for _, count in counts.items())
    items = []
    for mode, count in ranked:
        meta = taxonomy.get(mode)
        items.append(
            FailureClusterRow(
                failure_mode=mode,
                failure_mode_id=meta.id if meta else None,
                title=meta.title if meta else None,
                status=str(meta.status) if meta else "unknown",
                count=count,
                rate=_rate(count, total),
                examples=examples[mode],
            )
        )
    return FailureClustersResponse(
        workspace_id=workspace_id,
        window=MetricsWindow(start=start, end=end),
        experiment_id=experiment_id,
        total=total,
        items=items,
    )


def _taxonomy_index(storage: StorageInterface, workspace_id: str) -> dict[str, FailureMode]:
    """Map a failure-mode slug → its taxonomy entry, so clusters keyed by the
    stable slug carried on grades can be enriched with title/status/id."""
    with storage.open(workspace_id) as scope:
        modes = [
            m for m in scope.list_entities(FailureMode, ListFilter()) if isinstance(m, FailureMode)
        ]
    return {m.slug: m for m in modes}


def _filtered_traces(
    storage: StorageInterface,
    workspace_id: str,
    start: datetime | None,
    end: datetime | None,
    experiment_id: str | None,
) -> list[Trace]:
    """Trace-JSON sweep for views without a Postgres hot path (e.g. failure
    clusters). Filters by experiment via the mapper's ``run.experiment_id``
    column alias, then narrows by the time window in Python.

    The Postgres rollups (pass-rate/cost/token/…) go through dedicated storage
    methods instead; this fallback only backs the per-mode example drill-down
    that the aggregated fact tables don't yet carry."""
    trace_filter = {"run.experiment_id": experiment_id} if experiment_id is not None else {}
    with storage.open(workspace_id) as scope:
        traces = [
            trace
            for trace in scope.list_entities(Trace, ListFilter(where=trace_filter))
            if isinstance(trace, Trace)
        ]
    return [
        trace
        for trace in traces
        if (start is None or trace.environment.started_at >= start)
        and (end is None or trace.environment.started_at <= end)
    ]


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
