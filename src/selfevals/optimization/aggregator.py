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
from typing import TYPE_CHECKING, Any

from selfevals.graders._confusion import ConfusionReport, confusion_from_pairs
from selfevals.graders.base import BreakdownNode, GradeLabel, GradeResult
from selfevals.graders.classification import parse_cell_key
from selfevals.schemas.trace import LLMCallSpan

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
    cache_hit_count: int = 0
    """Number of `LLMCallSpan`s in this case's traces that were cache hits."""
    llm_call_count: int = 0
    """Total `LLMCallSpan`s in this case's traces. Pairs with `cache_hit_count`
    so the signal reads as "N cache hits of M llm calls"."""
    breakdowns: list[BreakdownNode] = field(default_factory=list)
    """Flat list of every funnel `BreakdownNode` (one per grade that carried a
    breakdown, across all repetitions of this case). The aggregator rolls these
    up by `key` into `IterationAggregate.funnel`."""
    per_grader_label: dict[str, list[GradeLabel]] = field(default_factory=dict)
    """Per-grader labels, keyed by grader name, one entry per repetition (in rep
    order). `per_repetition_label` is the worst-of collapse across graders; this
    keeps each grader's verdict separate so the report can show a per-grader
    pass-rate and `primary_grader` can score against a single grader instead of
    the conjunctive worst-of. Empty for outcomes built without per-grader data
    (e.g. rehydrated from persisted metrics)."""
    failure_weights: dict[str, int] = field(default_factory=dict)
    """Copy of the case's `failure_weights` (per-mode business severity). Carried
    on the outcome so `aggregate_iteration` can weight this case's failure modes
    by its own weights — weights are per-case, so the global mode counter can't
    be multiplied by a single weight. Empty when the case declared none."""
    critical_failure_modes: list[str] = field(default_factory=list)
    """Copy of the case's `critical_failure_modes` (zero-tolerance modes). Used
    to tally `critical_failure_count`. Empty when the case declared none."""

    @property
    def errored(self) -> bool:
        """True when the first repetition's effective label is ERROR.

        pass@1 reads the first repetition, so an errored rep-0 means this case
        has no quality verdict to contribute — it is excluded from the pass@1
        denominator rather than counted as a 0.0 failure (see `_compute_metric`).
        """
        return bool(self.per_repetition_label) and self.per_repetition_label[0] == GradeLabel.ERROR

    @property
    def pass_at_1(self) -> float:
        if not self.per_repetition_label:
            return 0.0
        return 1.0 if self.per_repetition_label[0] == GradeLabel.PASS else 0.0

    def pass_at_1_for_grader(self, grader: str) -> float | None:
        """pass@1 against a single grader's first-repetition label.

        Returns `None` when this grader did not run on the case (so the caller
        can exclude the case from that grader's denominator rather than counting
        it as a failure). 1.0/0.0 when it did."""
        labels = self.per_grader_label.get(grader)
        if not labels:
            return None
        return 1.0 if labels[0] == GradeLabel.PASS else 0.0

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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FunnelNode:
        """Inverse of `to_dict` — rebuild a node (and its subtree) from storage.

        `IterationRecord.metrics.funnel` persists the `to_dict` form; this
        rehydrates it so a result reconstructed from storage carries the same
        funnel a live run would. Tolerant of missing optional keys.
        """
        return cls(
            key=str(data["key"]),
            count=int(data.get("count", 0)),
            mean_score=data.get("mean_score"),
            total_weight=float(data.get("total_weight", 0.0)),
            label_counts=dict(data.get("label_counts", {})),
            failure_mode_counts=dict(data.get("failure_mode_counts", {})),
            children={
                k: cls.from_dict(v) for k, v in (data.get("children") or {}).items()
            },
        )


@dataclass(frozen=True)
class IterationAggregate:
    primary_metric: str
    primary_value: float
    guardrails: dict[str, float] = field(default_factory=dict)
    reliability: dict[str, float] = field(default_factory=dict)
    failure_mode_counts: dict[str, int] = field(default_factory=dict)
    total_cost_usd: float = 0.0
    total_duration_ms: int = 0
    cache_hit_count: int = 0
    """LLM cache hits summed across all cases this iteration. Surfaces in the
    JSON report so consumers can tell cached responses from cold calls without
    reading raw traces."""
    llm_call_count: int = 0
    """Total LLM calls summed across all cases this iteration. Denominator for
    `cache_hit_count`."""
    case_count: int = 0
    case_outcomes: list[CaseOutcome] = field(default_factory=list)
    funnel: dict[str, FunnelNode] = field(default_factory=dict)
    """Rollup of grader funnel breakdowns by top-level `key`. Empty when no
    grader emitted a breakdown. Additive: the funnel never affects the
    primary/guardrail/reliability metrics or the decision — it is the
    drill-down the reporter and frontend render. See `_rollup_funnel`."""
    per_grader_pass_rate: dict[str, float] = field(default_factory=dict)
    """pass@1 of each grader on its own, keyed by grader name. The primary
    metric collapses graders worst-of (a case passes only if every grader
    passed); this surfaces the masked per-grader signal — e.g. "must_include
    0.90, format 0.40" when the worst-of pass@1 reads 0.40. The denominator for
    each grader is the cases that grader actually ran on. Additive and
    informational; never changes the primary metric or the decision."""
    primary_grader: str | None = None
    """When set, the primary metric was scored against this single grader's
    label instead of the conjunctive worst-of. Echoed here so the report and
    decision trail can say which grader drove the number. `None` = worst-of
    (the default, conjunctive pass@1)."""
    error_rate: float = 0.0
    """Fraction of cases whose first repetition errored (effective rep-0 label
    is ERROR), over the total case count. These cases are excluded from the
    pass@1 denominator, so error_rate is the separate honest signal that says
    "X% of cases never produced a verdict" rather than silently dragging pass@1
    toward 0. 0.0 when nothing errored or there are no cases."""
    confusion: ConfusionReport | None = None
    """Aggregated NxN confusion matrix + per-class P/R/F1 + macro-F1, built from
    every `ClassificationGrader` cell (`cell:<expected>-><predicted>` breakdown
    keys) across all cases and repetitions this iteration. `None` when no
    `confusion` grader ran — additive and informational, never affects the
    primary/guardrail/reliability metrics or the decision. See `_rollup_confusion`."""

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


def _llm_call_and_cache_hit_count(traces: list[Trace]) -> tuple[int, int]:
    """Count LLM call spans and how many of them were cache hits."""
    llm_calls = 0
    cache_hits = 0
    for trace in traces:
        for span in trace.spans:
            if isinstance(span, LLMCallSpan):
                llm_calls += 1
                if span.cache_hit:
                    cache_hits += 1
    return llm_calls, cache_hits


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
    per_grader: dict[str, list[GradeLabel]] = {}
    reps = case_run.repetitions
    for idx, results in enumerate(grade_results_per_rep):
        # A non-null RepetitionResult.error means the adapter/executor failed for
        # this repetition; treat it as ERROR even when no grader tagged it so the
        # case is excluded from the pass@1 denominator rather than scored 0.0.
        rep_errored = idx < len(reps) and reps[idx].error is not None
        labels.append(GradeLabel.ERROR if rep_errored else _pick_label(results))
        scores.append(_pick_score(results))
        for r in results:
            failure_modes.extend(r.failure_modes)
            if r.breakdown is not None:
                breakdowns.append(r.breakdown)
            # Keep each grader's verdict separate, in rep order, so per-grader
            # reporting and `primary_grader` scoring can bypass the worst-of.
            per_grader.setdefault(r.grader, []).append(r.label)
    traces = [rep.trace for rep in case_run.repetitions]
    cost, duration = _trace_cost_and_duration(traces)
    llm_calls, cache_hits = _llm_call_and_cache_hit_count(traces)
    return CaseOutcome(
        case_id=case.id,
        per_repetition_label=labels,
        per_repetition_score=scores,
        failure_modes=failure_modes,
        cost_usd=cost,
        duration_ms=duration,
        cache_hit_count=cache_hits,
        llm_call_count=llm_calls,
        breakdowns=breakdowns,
        per_grader_label=per_grader,
        failure_weights=dict(case.failure_weights),
        critical_failure_modes=list(case.critical_failure_modes),
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


def _collect_cell_pairs(node: BreakdownNode, pairs: list[tuple[str, str]]) -> None:
    """Recursively gather `(expected, predicted)` pairs from `cell:` nodes.

    The cell node can sit directly under the `classification` root (single-turn)
    or be grafted under a `turn_N` node by the multi-turn collapse, so the walk
    must recurse rather than only inspect top-level keys.
    """
    pair = parse_cell_key(node.key)
    if pair is not None:
        pairs.append(pair)
    for child in node.children:
        _collect_cell_pairs(child, pairs)


def _rollup_confusion(case_outcomes: list[CaseOutcome]) -> ConfusionReport | None:
    """Build the iteration's NxN confusion matrix from classification cells.

    Each `ClassificationGrader` grade carries a breakdown with a
    `cell:<expected>-><predicted>` node (one per repetition), nested under a
    `classification` root or — for conversation cases — grafted under a `turn_N`
    node. This walks every case's breakdown tree, parses those keys back into
    `(expected, predicted)` pairs, and feeds them to the shared
    `confusion_from_pairs` — the same pure helper the calibration report uses, so
    the F1 formula is defined once. Returns `None` when no cell was emitted (no
    `confusion` grader ran), so the reporter omits the section.
    """
    pairs: list[tuple[str, str]] = []
    for outcome in case_outcomes:
        for node in outcome.breakdowns:
            _collect_cell_pairs(node, pairs)
    if not pairs:
        return None
    return confusion_from_pairs(pairs)


def aggregate_iteration(
    *,
    case_outcomes: list[CaseOutcome],
    primary_metric: str = "pass@1",
    reliability_metrics: list[str] | None = None,
    primary_grader: str | None = None,
) -> IterationAggregate:
    """Roll per-case outcomes into a single IterationAggregate.

    `primary_grader`, when set, scores the primary metric against that one
    grader's label instead of the conjunctive worst-of across all graders.
    Only `pass@1`/`pass@k`/`pass^k` honour it — those are the only metrics
    expressed in terms of per-rep pass/fail. Reliability metrics always use the
    worst-of labels (they measure cross-rep stability, not per-grader signal).
    """
    reliability_metrics = reliability_metrics or []
    n = len(case_outcomes)
    if n == 0:
        return IterationAggregate(
            primary_metric=primary_metric, primary_value=0.0, primary_grader=primary_grader
        )

    primary_value = _compute_metric(primary_metric, case_outcomes, primary_grader=primary_grader)
    reliability = {m: _compute_metric(m, case_outcomes) for m in reliability_metrics}
    per_grader_pass_rate = _per_grader_pass_rate(case_outcomes)
    failure_counter: Counter[str] = Counter()
    # Severity-weighted accuracy (G1). Weights live per-case, so the global mode
    # counter cannot be multiplied by a single weight — sum per outcome. critical
    # modes are likewise per-case. See EvalCase.failure_weights / .critical_*.
    weighted_failure_total = 0.0
    critical_failure_count = 0
    for outcome in case_outcomes:
        per_case_counts: Counter[str] = Counter()
        for mode in outcome.failure_modes:
            failure_counter[mode] += 1
            per_case_counts[mode] += 1
        for mode, count in per_case_counts.items():
            weighted_failure_total += count * outcome.failure_weights.get(mode, 0)
            if mode in outcome.critical_failure_modes:
                critical_failure_count += count
    total_cost = sum(o.cost_usd for o in case_outcomes)
    total_duration = sum(o.duration_ms for o in case_outcomes)
    total_cache_hits = sum(o.cache_hit_count for o in case_outcomes)
    total_llm_calls = sum(o.llm_call_count for o in case_outcomes)
    error_rate = sum(1 for o in case_outcomes if o.errored) / n
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
    # Severity-weighted accuracy as guardrails (G1). Published so the
    # DecisionMatrix resolves them by name (matrix._guardrails_violated) without
    # any change there — a critical tier is a guardrail `critical_failure_count
    # == 0`. weighted_* only when some weight applied; critical_failure_count is
    # published ALWAYS (even 0.0), else a missing value reads as "passing" and
    # the zero-tolerance gate silently no-ops exactly when it matters.
    if weighted_failure_total > 0:
        guardrails["weighted_failure_total"] = weighted_failure_total
        guardrails["weighted_failure_per_case"] = weighted_failure_total / n
    guardrails["critical_failure_count"] = float(critical_failure_count)
    return IterationAggregate(
        primary_metric=primary_metric,
        primary_value=primary_value,
        guardrails=guardrails,
        reliability=reliability,
        failure_mode_counts=dict(failure_counter),
        total_cost_usd=total_cost,
        total_duration_ms=total_duration,
        cache_hit_count=total_cache_hits,
        llm_call_count=total_llm_calls,
        case_count=n,
        case_outcomes=case_outcomes,
        funnel=_rollup_funnel(case_outcomes),
        per_grader_pass_rate=per_grader_pass_rate,
        primary_grader=primary_grader,
        error_rate=error_rate,
        confusion=_rollup_confusion(case_outcomes),
    )


def _per_grader_pass_rate(outcomes: list[CaseOutcome]) -> dict[str, float]:
    """pass@1 of each grader on its own, across the cases it ran on.

    Each grader's denominator is the cases where it produced a label (via
    `pass_at_1_for_grader` returning non-None), so a grader scoped to a subset
    of cases isn't penalised for cases it never graded. Empty when no outcome
    carried per-grader labels (e.g. rehydrated-from-metrics aggregates)."""
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    for o in outcomes:
        for grader in o.per_grader_label:
            value = o.pass_at_1_for_grader(grader)
            if value is None:
                continue
            sums[grader] = sums.get(grader, 0.0) + value
            counts[grader] = counts.get(grader, 0) + 1
    return {g: sums[g] / counts[g] for g in sums if counts[g] > 0}


def _compute_metric(
    metric: str, outcomes: list[CaseOutcome], *, primary_grader: str | None = None
) -> float:
    """Compute pass@k / pass^k / consistency_rate against a case list.

    When `primary_grader` is set and `metric` is a pass-style metric, scoring
    uses that grader's per-rep labels instead of the worst-of collapse. A case
    the grader didn't run on is excluded from the denominator (it has no verdict
    to contribute), matching `_per_grader_pass_rate`."""
    if not outcomes:
        return 0.0
    if primary_grader is not None and (
        metric == "pass@1" or metric.startswith(("pass@", "pass^"))
    ):
        return _compute_pass_metric_for_grader(metric, outcomes, primary_grader)
    n = len(outcomes)
    if metric == "pass@1":
        # Exclude errored cases from the denominator: an errored case has no
        # quality verdict, so counting it as 0.0 would conflate "the agent
        # answered wrong" with "the run blew up". error_rate carries that signal
        # separately. If every case errored, there's nothing to score → 0.0.
        scored = [o for o in outcomes if not o.errored]
        if not scored:
            return 0.0
        return sum(o.pass_at_1 for o in scored) / len(scored)
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


def _compute_pass_metric_for_grader(
    metric: str, outcomes: list[CaseOutcome], grader: str
) -> float:
    """pass@1 / pass@k / pass^k scored against one grader's per-rep labels.

    Mirrors the worst-of pass logic in `_compute_metric` but reads
    `per_grader_label[grader]` instead of `per_repetition_label`. Cases the
    grader never ran on are skipped (no verdict → not in the denominator); the
    result is 0.0 when no case carries a label for this grader."""
    windows: list[list[GradeLabel]] = [
        o.per_grader_label[grader] for o in outcomes if o.per_grader_label.get(grader)
    ]
    if not windows:
        return 0.0
    n = len(windows)
    if metric == "pass@1":
        return sum(1 for w in windows if w[0] == GradeLabel.PASS) / n
    if metric.startswith("pass@"):
        k = int(metric.split("@", 1)[1])
        return sum(1 for w in windows if GradeLabel.PASS in w[:k]) / n
    # pass^k: all k reps pass (cases with fewer than k reps can't satisfy it).
    k = int(metric.split("^", 1)[1])
    return sum(
        1 for w in windows if len(w) >= k and all(label == GradeLabel.PASS for label in w[:k])
    ) / n


class Aggregator:
    """Stateless helper bundling _outcome_for + aggregate_iteration."""

    @staticmethod
    def case_outcome(
        case: EvalCase,
        case_run: CaseRun,
        grade_results_per_rep: list[list[GradeResult]],
    ) -> CaseOutcome:
        return _outcome_for(case, case_run, grade_results_per_rep)
