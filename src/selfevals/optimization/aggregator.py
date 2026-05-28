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

from selfevals.graders.base import BreakdownNode, GradeLabel, GradeResult

if TYPE_CHECKING:
    from selfevals.runner.executor import CaseRun
    from selfevals.schemas.eval_case import EvalCase
    from selfevals.schemas.trace import Trace


@dataclass(frozen=True)
class CaseOutcome:
    case_id: str
    per_repetition_label: list[GradeLabel]
    per_repetition_score: list[float]
    failure_modes: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    duration_ms: int = 0
    breakdowns: list[BreakdownNode] = field(default_factory=list)
    """Flat list of every funnel `BreakdownNode` (one per grade that carried a
    breakdown, across all repetitions of this case). The aggregator rolls these
    up by `key` into `IterationAggregate.funnel`."""

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
class FunnelNode:
    """Rolled-up funnel node for one `key` across an iteration.

    Aggregates every `BreakdownNode` that shared this `key` (across cases and
    repetitions) into the drill-down view the reporter and frontend read:

    - `count`: how many breakdown nodes contributed.
    - `mean_score`: weight-weighted mean of the contributing nodes' scores
      (nodes with `score=None` are skipped; `None` when nobody scored).
    - `total_weight`: sum of the contributing nodes' weights.
    - `label_counts`: tally of per-node labels (label-only nodes included).
    - `failure_mode_counts`: tally of failure-mode tags on this node.
    - `children`: nested rolled-up nodes, preserving the funnel shape.

    Purely informational: nothing here changes the iteration's pass/fail.
    """

    key: str
    count: int = 0
    mean_score: float | None = None
    total_weight: float = 0.0
    label_counts: dict[str, int] = field(default_factory=dict)
    failure_mode_counts: dict[str, int] = field(default_factory=dict)
    children: dict[str, FunnelNode] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        """JSON-serializable view (recurses into children)."""
        return {
            "key": self.key,
            "count": self.count,
            "mean_score": self.mean_score,
            "total_weight": self.total_weight,
            "label_counts": dict(self.label_counts),
            "failure_mode_counts": dict(self.failure_mode_counts),
            "children": {k: v.to_dict() for k, v in self.children.items()},
        }


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
    funnel: dict[str, FunnelNode] = field(default_factory=dict)
    """Rollup of grader funnel breakdowns by top-level `key`. Empty when no
    grader emitted a breakdown. Additive: the funnel never affects the
    primary/guardrail/reliability metrics or the decision — it is the
    drill-down the reporter and frontend render. See `_rollup_funnel`."""

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


def _percentile(sorted_values: list[float], q: float) -> float:
    """Linear-interpolated percentile of an already-sorted, non-empty list.

    `q` is a fraction in [0, 1] (e.g. 0.95 for p95). Matches the common
    "type 7" interpolation so a single value yields itself and the result is
    stable across runs.
    """
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = q * (len(sorted_values) - 1)
    low = int(rank)
    frac = rank - low
    if low + 1 >= len(sorted_values):
        return sorted_values[-1]
    return sorted_values[low] + frac * (sorted_values[low + 1] - sorted_values[low])


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
    breakdowns: list[BreakdownNode] = []
    for results in grade_results_per_rep:
        labels.append(_pick_label(results))
        scores.append(_pick_score(results))
        for r in results:
            failure_modes.extend(r.failure_modes)
            if r.breakdown is not None:
                breakdowns.append(r.breakdown)
    traces = [rep.trace for rep in case_run.repetitions]
    cost, duration = _trace_cost_and_duration(traces)
    return CaseOutcome(
        case_id=case.id,
        per_repetition_label=labels,
        per_repetition_score=scores,
        failure_modes=failure_modes,
        cost_usd=cost,
        duration_ms=duration,
        breakdowns=breakdowns,
    )


@dataclass
class _FunnelAccumulator:
    """Mutable scratch node used while rolling up; frozen into a FunnelNode."""

    key: str
    count: int = 0
    weighted_score_sum: float = 0.0
    scored_weight: float = 0.0
    total_weight: float = 0.0
    label_counts: Counter[str] = field(default_factory=Counter)
    failure_mode_counts: Counter[str] = field(default_factory=Counter)
    children: dict[str, _FunnelAccumulator] = field(default_factory=dict)

    def add(self, node: BreakdownNode) -> None:
        self.count += 1
        self.total_weight += node.weight
        if node.score is not None:
            # Weight the score contribution; weight=0 nodes are advisory and
            # contribute to counts/failure_modes but not to the mean score.
            self.weighted_score_sum += node.score * node.weight
            self.scored_weight += node.weight
        if node.label is not None:
            self.label_counts[node.label.value] += 1
        for mode in node.failure_modes:
            self.failure_mode_counts[mode] += 1
        for child in node.children:
            acc = self.children.get(child.key)
            if acc is None:
                acc = _FunnelAccumulator(key=child.key)
                self.children[child.key] = acc
            acc.add(child)

    def freeze(self) -> FunnelNode:
        mean_score = self.weighted_score_sum / self.scored_weight if self.scored_weight else None
        return FunnelNode(
            key=self.key,
            count=self.count,
            mean_score=mean_score,
            total_weight=self.total_weight,
            label_counts=dict(self.label_counts),
            failure_mode_counts=dict(self.failure_mode_counts),
            children={k: v.freeze() for k, v in self.children.items()},
        )


def _rollup_funnel(case_outcomes: list[CaseOutcome]) -> dict[str, FunnelNode]:
    """Roll every case's breakdown trees up by top-level `key`.

    Same-key nodes are merged (count + weighted-mean score + label and
    failure-mode tallies), recursing into children so the funnel keeps its
    shape. Returns an empty dict when no breakdowns were emitted, mirroring how
    cost/time sections are omitted when there is no data.
    """
    roots: dict[str, _FunnelAccumulator] = {}
    for outcome in case_outcomes:
        for node in outcome.breakdowns:
            acc = roots.get(node.key)
            if acc is None:
                acc = _FunnelAccumulator(key=node.key)
                roots[node.key] = acc
            acc.add(node)
    return {key: acc.freeze() for key, acc in roots.items()}


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
        # Percentiles over the per-case latency distribution. p95 is the
        # guardrail the experiment contract reads (§6); p50/p99 round out the
        # tail picture. The DecisionMatrix looks these up by name.
        latencies = sorted(float(o.duration_ms) for o in case_outcomes)
        guardrails["latency_ms_p50"] = _percentile(latencies, 0.50)
        guardrails["latency_ms_p95"] = _percentile(latencies, 0.95)
        guardrails["latency_ms_p99"] = _percentile(latencies, 0.99)
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
        funnel=_rollup_funnel(case_outcomes),
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
