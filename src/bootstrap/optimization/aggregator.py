"""Aggregator: roll per-case GradeResults into IterationMetrics.

The aggregator takes a list of `(case, [RepetitionResult,...])` plus the
graders that ran, and produces an `IterationAggregate` with:

- primary metric value (pass@1 by default; pass^k optional)
- guardrail metric values (cost_usd_per_case, latency_ms_p95, ...)
- reliability metrics (pass@k, pass^k, consistency_rate)
- failure_mode counts (from DeterministicGrader tags)
- cost / duration totals

These feed straight into `IterationMetrics` and the decision matrix.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from statistics import mean
from typing import TYPE_CHECKING

from bootstrap.graders.base import GradeLabel, GradeResult

if TYPE_CHECKING:
    from bootstrap.runner.executor import CaseRun
    from bootstrap.schemas.eval_case import EvalCase
    from bootstrap.schemas.trace import Trace


@dataclass(frozen=True)
class CaseOutcome:
    case_id: str
    per_repetition_label: list[GradeLabel]
    per_repetition_score: list[float]
    failure_modes: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    duration_ms: int = 0

    @property
    def pass_at_1(self) -> float:
        if not self.per_repetition_label:
            return 0.0
        return 1.0 if self.per_repetition_label[0] == GradeLabel.PASS else 0.0

    @property
    def consistency_rate(self) -> float:
        if not self.per_repetition_label:
            return 0.0
        passes = sum(1 for label in self.per_repetition_label if label == GradeLabel.PASS)
        return passes / len(self.per_repetition_label)


@dataclass(frozen=True)
class IterationAggregate:
    primary_metric: str
    primary_value: float
    guardrails: dict[str, float] = field(default_factory=dict)
    reliability: dict[str, float] = field(default_factory=dict)
    failure_mode_counts: dict[str, int] = field(default_factory=dict)
    total_cost_usd: float = 0.0
    total_duration_ms: int = 0
    case_count: int = 0
    case_outcomes: list[CaseOutcome] = field(default_factory=list)

    @property
    def fail_rate(self) -> float:
        """Fraction of cases whose first repetition did not pass.

        This is the trigger signal for staging error analysis (§9): a healthy
        run stays below the configured threshold and is never staged.
        """
        if not self.case_outcomes:
            return 0.0
        failed = sum(1 for o in self.case_outcomes if o.pass_at_1 == 0.0)
        return failed / len(self.case_outcomes)


def _trace_cost_and_duration(traces: list[Trace]) -> tuple[float, int]:
    cost = sum(t.metrics.total_cost_usd for t in traces)
    duration = sum(t.metrics.total_duration_ms for t in traces)
    return cost, duration


def _pick_label(results: list[GradeResult]) -> GradeLabel:
    """Worst-of policy for combining multiple graders on one repetition.

    Order of severity: ERROR > FAIL > PARTIAL > SKIPPED > PASS.
    """
    if not results:
        return GradeLabel.SKIPPED
    severity = {
        GradeLabel.ERROR: 4,
        GradeLabel.FAIL: 3,
        GradeLabel.PARTIAL: 2,
        GradeLabel.SKIPPED: 1,
        GradeLabel.PASS: 0,
    }
    return max(results, key=lambda r: severity[r.label]).label


def _pick_score(results: list[GradeResult]) -> float:
    """Mean of available scores; if none provide a score, derive from labels.

    For label-only graders we map pass→1.0, partial→0.5, fail/error/skipped→0.0.
    """
    explicit = [r.score for r in results if r.score is not None]
    if explicit:
        return float(mean(explicit))
    if not results:
        return 0.0
    map_score = {
        GradeLabel.PASS: 1.0,
        GradeLabel.PARTIAL: 0.5,
        GradeLabel.FAIL: 0.0,
        GradeLabel.ERROR: 0.0,
        GradeLabel.SKIPPED: 0.0,
    }
    return float(mean(map_score[r.label] for r in results))


def _outcome_for(
    case: EvalCase,
    case_run: CaseRun,
    grade_results_per_rep: list[list[GradeResult]],
) -> CaseOutcome:
    labels: list[GradeLabel] = []
    scores: list[float] = []
    failure_modes: list[str] = []
    for results in grade_results_per_rep:
        labels.append(_pick_label(results))
        scores.append(_pick_score(results))
        for r in results:
            failure_modes.extend(r.failure_modes)
    traces = [rep.trace for rep in case_run.repetitions]
    cost, duration = _trace_cost_and_duration(traces)
    return CaseOutcome(
        case_id=case.id,
        per_repetition_label=labels,
        per_repetition_score=scores,
        failure_modes=failure_modes,
        cost_usd=cost,
        duration_ms=duration,
    )


def aggregate_iteration(
    *,
    case_outcomes: list[CaseOutcome],
    primary_metric: str = "pass@1",
    reliability_metrics: list[str] | None = None,
) -> IterationAggregate:
    """Roll per-case outcomes into a single IterationAggregate."""
    reliability_metrics = reliability_metrics or []
    n = len(case_outcomes)
    if n == 0:
        return IterationAggregate(primary_metric=primary_metric, primary_value=0.0)

    primary_value = _compute_metric(primary_metric, case_outcomes)
    reliability = {m: _compute_metric(m, case_outcomes) for m in reliability_metrics}
    failure_counter: Counter[str] = Counter()
    for outcome in case_outcomes:
        for mode in outcome.failure_modes:
            failure_counter[mode] += 1
    total_cost = sum(o.cost_usd for o in case_outcomes)
    total_duration = sum(o.duration_ms for o in case_outcomes)
    guardrails: dict[str, float] = {}
    if total_cost > 0:
        guardrails["cost_usd_per_case"] = total_cost / n
    if total_duration > 0:
        guardrails["latency_ms_per_case_avg"] = total_duration / n
    return IterationAggregate(
        primary_metric=primary_metric,
        primary_value=primary_value,
        guardrails=guardrails,
        reliability=reliability,
        failure_mode_counts=dict(failure_counter),
        total_cost_usd=total_cost,
        total_duration_ms=total_duration,
        case_count=n,
        case_outcomes=case_outcomes,
    )


def _compute_metric(metric: str, outcomes: list[CaseOutcome]) -> float:
    """Compute pass@k / pass^k / consistency_rate against a case list."""
    if not outcomes:
        return 0.0
    n = len(outcomes)
    if metric == "pass@1":
        return sum(o.pass_at_1 for o in outcomes) / n
    if metric.startswith("pass@"):
        k = int(metric.split("@", 1)[1])
        # pass@k: probability at least one of k passes (>= 1 success in first k reps).
        hits = 0
        for o in outcomes:
            window = o.per_repetition_label[:k]
            if not window:
                continue
            if GradeLabel.PASS in window:
                hits += 1
        return hits / n
    if metric.startswith("pass^"):
        k = int(metric.split("^", 1)[1])
        # pass^k: all k repetitions pass.
        hits = 0
        for o in outcomes:
            window = o.per_repetition_label[:k]
            if len(window) < k:
                continue
            if all(label == GradeLabel.PASS for label in window):
                hits += 1
        return hits / n
    if metric == "consistency_rate":
        return mean(o.consistency_rate for o in outcomes)
    if metric == "stability_score":
        return mean(o.consistency_rate for o in outcomes)
    if metric == "recovery_rate":
        # Of cases that failed on rep 0, fraction that pass on rep 1+.
        denom = sum(
            1
            for o in outcomes
            if o.per_repetition_label and o.per_repetition_label[0] != GradeLabel.PASS
        )
        if denom == 0:
            return 0.0
        recovered = sum(
            1
            for o in outcomes
            if o.per_repetition_label
            and o.per_repetition_label[0] != GradeLabel.PASS
            and any(label == GradeLabel.PASS for label in o.per_repetition_label[1:])
        )
        return recovered / denom
    raise ValueError(f"unsupported aggregate metric: {metric!r}")


class Aggregator:
    """Stateless helper bundling _outcome_for + aggregate_iteration."""

    @staticmethod
    def case_outcome(
        case: EvalCase,
        case_run: CaseRun,
        grade_results_per_rep: list[list[GradeResult]],
    ) -> CaseOutcome:
        return _outcome_for(case, case_run, grade_results_per_rep)
