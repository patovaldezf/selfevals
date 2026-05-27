"""Pure helpers for cost & time aggregation over an OptimizationResult.

These helpers exist so both the markdown and the JSON reporter compute
identical numbers without duplicating logic. Each function returns
`None` when the underlying data is unavailable (e.g. an echo agent with
no LLM calls reports zero cost — we surface that as "no data" rather
than a misleading "$0.00").

No I/O, no global state. Easy to unit-test in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from selfevals.optimization.loop import OptimizationResult


@dataclass(frozen=True)
class CostTimeSummary:
    """Aggregate cost/time totals + per-iteration / per-case means.

    Fields are `None` when the underlying data is missing across the
    whole result (no traces reported any cost, etc.).
    """

    cost_total_usd: float | None
    cost_per_iteration_usd: float | None
    cost_per_case_usd: float | None

    time_total_seconds: float | None
    time_per_iteration_seconds: float | None
    time_per_case_seconds: float | None

    iterations: int
    cases_run: int

    @property
    def has_cost(self) -> bool:
        return self.cost_total_usd is not None

    @property
    def has_time(self) -> bool:
        return self.time_total_seconds is not None

    @property
    def has_any(self) -> bool:
        return self.has_cost or self.has_time


def compute_total_cost(result: OptimizationResult) -> float | None:
    """Sum `total_cost_usd` over all iterations.

    Returns `None` if no iteration reported any cost (typical for offline
    echo agents). Returning `None` keeps callers from rendering
    placeholder "$0.00" rows that look like real data.
    """
    if not result.iterations:
        return None
    has_any = False
    total = 0.0
    for it in result.iterations:
        cost = it.aggregate.total_cost_usd
        if cost > 0:
            has_any = True
        total += cost
    return total if has_any else None


def compute_total_time_seconds(result: OptimizationResult) -> float | None:
    """Sum iteration durations (in seconds). Returns `None` when no
    iteration reports any duration."""
    if not result.iterations:
        return None
    has_any = False
    total_ms = 0
    for it in result.iterations:
        ms = it.aggregate.total_duration_ms
        if ms > 0:
            has_any = True
        total_ms += ms
    return total_ms / 1000.0 if has_any else None


def compute_total_cases(result: OptimizationResult) -> int:
    """Total case executions across all iterations (sum of case_count)."""
    return sum(it.aggregate.case_count for it in result.iterations)


def compute_cost_time_summary(result: OptimizationResult) -> CostTimeSummary:
    """Single struct holding all cost/time numbers for the report."""
    n_iter = len(result.iterations)
    n_cases = compute_total_cases(result)

    total_cost = compute_total_cost(result)
    total_time = compute_total_time_seconds(result)

    cost_per_iter = (total_cost / n_iter) if (total_cost is not None and n_iter > 0) else None
    cost_per_case = (total_cost / n_cases) if (total_cost is not None and n_cases > 0) else None
    time_per_iter = (total_time / n_iter) if (total_time is not None and n_iter > 0) else None
    time_per_case = (total_time / n_cases) if (total_time is not None and n_cases > 0) else None

    return CostTimeSummary(
        cost_total_usd=total_cost,
        cost_per_iteration_usd=cost_per_iter,
        cost_per_case_usd=cost_per_case,
        time_total_seconds=total_time,
        time_per_iteration_seconds=time_per_iter,
        time_per_case_seconds=time_per_case,
        iterations=n_iter,
        cases_run=n_cases,
    )
