"""Workspace metrics queries for production agent monitoring.

Postgres-backed installs use normalized trace fact tables. SQLite keeps a
small-data fallback over canonical Trace JSON so the local quickstart remains
usable without extra services.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable
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
from selfevals.schemas.trace import LLMCallSpan, ToolCallSpan, Trace
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
    hot = _hot_method(storage, "pass_rate_metrics")
    if hot is not None:
        rows = hot(
            workspace_id=workspace_id,
            start=start,
            end=end,
            experiment_id=experiment_id,
            grader=grader,
        )
    else:
        counts: Counter[tuple[str, str]] = Counter()
        for trace in _filtered_traces(storage, workspace_id, start, end, experiment_id):
            for result in trace.grader_results:
                if grader is not None and result.grader != grader:
                    continue
                counts[(result.grader, result.label)] += 1
        rows = [
            {"grader": key[0], "label": key[1], "count": count}
            for key, count in counts.most_common()
        ]
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
    hot = _hot_method(storage, "failure_mode_metrics")
    if hot is not None:
        rows = hot(
            workspace_id=workspace_id,
            start=start,
            end=end,
            experiment_id=experiment_id,
            grader=grader,
        )
    else:
        counts: Counter[str] = Counter()
        for trace in _filtered_traces(storage, workspace_id, start, end, experiment_id):
            for result in trace.grader_results:
                if grader is not None and result.grader != grader:
                    continue
                counts.update(result.failure_modes)
        rows = [{"failure_mode": mode, "count": count} for mode, count in counts.most_common()]
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


def tool_metrics(
    storage: StorageInterface,
    *,
    workspace_id: str,
    start: datetime | None = None,
    end: datetime | None = None,
    experiment_id: str | None = None,
    tool_name: str | None = None,
) -> ToolMetricsResponse:
    hot = _hot_method(storage, "tool_metrics")
    if hot is not None:
        rows = hot(
            workspace_id=workspace_id,
            start=start,
            end=end,
            experiment_id=experiment_id,
            tool_name=tool_name,
        )
    else:
        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        for trace in _filtered_traces(storage, workspace_id, start, end, experiment_id):
            for span in trace.spans:
                if not isinstance(span, ToolCallSpan):
                    continue
                if tool_name is not None and span.tool_name != tool_name:
                    continue
                key = (span.tool_name, str(span.status))
                bucket = grouped.setdefault(
                    key,
                    {
                        "tool_name": span.tool_name,
                        "status": str(span.status),
                        "count": 0,
                        "error_count": 0,
                        "duration_total": 0,
                        "retry_count": 0,
                    },
                )
                bucket["count"] += 1
                bucket["error_count"] += 1 if str(span.status) != "ok" or span.error else 0
                bucket["duration_total"] += span.duration_ms
                bucket["retry_count"] += len(span.retry_chain)
        rows = []
        for bucket in grouped.values():
            count = int(bucket["count"])
            rows.append(
                {
                    "tool_name": bucket["tool_name"],
                    "status": bucket["status"],
                    "count": count,
                    "error_count": bucket["error_count"],
                    "avg_duration_ms": bucket["duration_total"] / count if count else None,
                    "retry_count": bucket["retry_count"],
                }
            )
        rows.sort(key=lambda row: (-int(row["count"]), str(row["tool_name"]), str(row["status"])))
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
    hot = _hot_method(storage, "cost_metrics")
    if hot is not None:
        rows = hot(
            workspace_id=workspace_id,
            start=start,
            end=end,
            experiment_id=experiment_id,
            model=model,
        )
    else:
        grouped: dict[tuple[str, str], dict[str, Any]] = defaultdict(
            lambda: {"call_count": 0, "total_cost_usd": 0.0}
        )
        for trace in _filtered_traces(storage, workspace_id, start, end, experiment_id):
            for span in trace.spans:
                if not isinstance(span, LLMCallSpan):
                    continue
                if model is not None and span.model != model:
                    continue
                bucket = grouped[(span.provider, span.model)]
                bucket["provider"] = span.provider
                bucket["model"] = span.model
                bucket["call_count"] += 1
                bucket["total_cost_usd"] += span.cost_usd.total
        rows = []
        for bucket in grouped.values():
            count = int(bucket["call_count"])
            total_cost = float(bucket["total_cost_usd"])
            rows.append(
                {
                    "provider": bucket["provider"],
                    "model": bucket["model"],
                    "call_count": count,
                    "total_cost_usd": total_cost,
                    "avg_cost_usd": total_cost / count if count else 0.0,
                }
            )
        rows.sort(key=lambda row: (-float(row["total_cost_usd"]), str(row["provider"]), str(row["model"])))
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
    hot = _hot_method(storage, "token_metrics")
    if hot is not None:
        rows = hot(
            workspace_id=workspace_id,
            start=start,
            end=end,
            experiment_id=experiment_id,
            model=model,
        )
    else:
        grouped: dict[tuple[str, str], dict[str, Any]] = defaultdict(
            lambda: {
                "call_count": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "reasoning_tokens": 0,
                "total_tokens": 0,
            }
        )
        for trace in _filtered_traces(storage, workspace_id, start, end, experiment_id):
            for span in trace.spans:
                if not isinstance(span, LLMCallSpan):
                    continue
                if model is not None and span.model != model:
                    continue
                bucket = grouped[(span.provider, span.model)]
                bucket["provider"] = span.provider
                bucket["model"] = span.model
                bucket["call_count"] += 1
                bucket["input_tokens"] += span.tokens.input
                bucket["output_tokens"] += span.tokens.output
                bucket["reasoning_tokens"] += span.tokens.reasoning
                bucket["total_tokens"] += span.tokens.total
        rows = list(grouped.values())
        rows.sort(key=lambda row: (-int(row["total_tokens"]), str(row["provider"]), str(row["model"])))
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
    hot = _hot_method(storage, "latency_metrics")
    if hot is not None:
        rows = hot(
            workspace_id=workspace_id,
            start=start,
            end=end,
            experiment_id=experiment_id,
        )
    else:
        trace_values: list[float] = []
        tool_values: list[float] = []
        ttft_values: list[float] = []
        for trace in _filtered_traces(storage, workspace_id, start, end, experiment_id):
            if trace.metrics.total_duration_ms:
                trace_values.append(float(trace.metrics.total_duration_ms))
            elif trace.environment.ended_at is not None:
                delta = trace.environment.ended_at - trace.environment.started_at
                trace_values.append(delta.total_seconds() * 1000)
            for span in trace.spans:
                if isinstance(span, ToolCallSpan):
                    tool_values.append(float(span.duration_ms))
                elif isinstance(span, LLMCallSpan) and span.time_to_first_token_ms is not None:
                    ttft_values.append(float(span.time_to_first_token_ms))
        rows = [
            _latency_row("trace_duration_ms", trace_values),
            _latency_row("tool_duration_ms", tool_values),
            _latency_row("ttft_ms", ttft_values),
        ]
        rows = [row for row in rows if int(row["count"]) > 0]
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


def _filtered_traces(
    storage: StorageInterface,
    workspace_id: str,
    start: datetime | None,
    end: datetime | None,
    experiment_id: str | None,
) -> list[Trace]:
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


def _hot_method(storage: StorageInterface, name: str) -> Callable[..., list[dict[str, Any]]] | None:
    method = getattr(storage, name, None)
    return method if callable(method) else None


def _rate(count: int, total: int) -> float:
    return count / total if total else 0.0


def _latency_row(metric: str, values: list[float]) -> dict[str, Any]:
    return {
        "metric": metric,
        "count": len(values),
        "p50_ms": _percentile(values, 0.50),
        "p95_ms": _percentile(values, 0.95),
        "p99_ms": _percentile(values, 0.99),
    }


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return ordered[lower]
    fraction = index - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _optional_float(value: Any) -> float | None:
    return float(value) if value is not None else None
