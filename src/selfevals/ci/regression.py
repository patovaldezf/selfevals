"""Pure regression-gate math.

`evaluate_regression(baseline, current, thresholds) -> RegressionResult` is the
whole gate: it takes the baseline's headline metrics and the current run's, and
decides whether the agent regressed on a FIXED set of cases. It is deliberately
agnostic of what the baseline is anchored to — the SF-4 design anchors it to a
*dataset* (the first run over `ds_xxx` becomes its baseline), but this function
never learns that; the caller loads a `DatasetBaseline`, builds a
`BaselineMetrics`, and passes the numbers in. That separation keeps the math
unit-testable without storage and lets the anchor change without touching it.

Three signals, each independently gated:

* **primary** (pass@1 by default): a drop beyond `primary_drop` is a regression.
  This is the headline "did the agent get worse overall" signal.
* **per-class F1** (from the confusion matrix): a per-label F1 drop beyond
  `per_class_f1_drop` is a regression even when the aggregate held — it catches
  a model that traded recall on one class for another and kept the average.
  Only labels present in BOTH baseline and current are compared; a label that
  vanished or appeared is reported as informational, never a hard fail (the case
  mix, not the agent, may have shifted).
* **error_rate**: a *rise* beyond `error_rate_rise` is a regression — the run
  blew up more often, which pass@1 alone hides (errored cases leave the pass@1
  denominator).

`tolerance` deadzones float noise so a `1e-12` wobble never trips the gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RegressionThresholds:
    """How much worse the current run may get before the gate fails.

    All values are absolute deltas in metric units (pass@1 and F1 live in
    [0, 1], so `0.02` is "two points"). Defaults are deliberately strict but not
    hair-trigger; override per-call for noisier suites.
    """

    primary_drop: float = 0.0
    """Max allowed drop in the primary metric. `0.0` = any real drop fails."""
    per_class_f1_drop: float = 0.05
    """Max allowed drop in any single class's F1 before it counts as a regression."""
    error_rate_rise: float = 0.0
    """Max allowed rise in error_rate. `0.0` = any real rise fails."""
    tolerance: float = 1e-9
    """Deadzone: deltas with magnitude below this are treated as no change."""


@dataclass(frozen=True)
class BaselineMetrics:
    """The metrics a run is graded against (or the current run's own metrics).

    Both sides of the comparison use this shape so the gate is symmetric.
    `per_class_f1` is extracted from a `ConfusionReport.to_dict()` payload (its
    `per_label_f1`), dropping `None` entries (a class with undefined P or R).
    """

    primary_metric: str
    primary_value: float
    error_rate: float = 0.0
    per_class_f1: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_confusion(
        cls,
        *,
        primary_metric: str,
        primary_value: float,
        error_rate: float = 0.0,
        confusion: dict[str, object] | None = None,
    ) -> BaselineMetrics:
        """Build metrics, pulling per-class F1 out of a serialized confusion report.

        `confusion` is the `ConfusionReport.to_dict()` form (or `None`). Only
        non-`None` per-label F1s are kept — a label whose precision or recall is
        undefined contributes no comparable F1.
        """
        per_class: dict[str, float] = {}
        if confusion:
            raw = confusion.get("per_label_f1")
            if isinstance(raw, dict):
                for label, value in raw.items():
                    if value is not None:
                        per_class[str(label)] = float(value)
        return cls(
            primary_metric=primary_metric,
            primary_value=primary_value,
            error_rate=error_rate,
            per_class_f1=per_class,
        )


@dataclass(frozen=True)
class RegressionFinding:
    """One line of the gate's verdict.

    `regressed=True` means this signal failed the gate. `regressed=False` with a
    populated `detail` is an informational note (improvement, or a class that
    appeared/vanished) — surfaced in the report but never flips the exit code.
    """

    signal: str
    """e.g. 'primary', 'error_rate', "f1[label]"."""
    baseline: float | None
    current: float | None
    delta: float | None
    regressed: bool
    detail: str


@dataclass(frozen=True)
class RegressionResult:
    """Verdict of the gate. `regressed` is the single bit the CLI maps to exit 1."""

    regressed: bool
    findings: list[RegressionFinding]

    @property
    def regressions(self) -> list[RegressionFinding]:
        """Only the findings that failed the gate."""
        return [f for f in self.findings if f.regressed]

    def summary(self) -> str:
        """One-line headline for the gate verdict."""
        if not self.regressed:
            return "no regression"
        n = len(self.regressions)
        return f"REGRESSION: {n} signal{'s' if n != 1 else ''} got worse"


def evaluate_regression(
    baseline_metrics: BaselineMetrics,
    current_metrics: BaselineMetrics,
    thresholds: RegressionThresholds | None = None,
) -> RegressionResult:
    """Compare current metrics against a baseline and decide if the agent regressed.

    Pure: no I/O, no clock, no storage. Deterministic in its inputs.

    A signal "regresses" when it moves in the worse direction by more than its
    threshold (with `tolerance` deadzoning float noise). Improvements and
    no-change are recorded as informational findings so the report reads as a
    full diff, not just the failures.
    """
    th = thresholds or RegressionThresholds()
    findings: list[RegressionFinding] = []
    any_regressed = False

    # --- primary (higher is better) -----------------------------------------
    primary_delta = current_metrics.primary_value - baseline_metrics.primary_value
    metric_name = baseline_metrics.primary_metric
    if current_metrics.primary_metric != baseline_metrics.primary_metric:
        # Comparing apples to oranges: don't silently pass. Flag it loudly.
        findings.append(
            RegressionFinding(
                signal="primary",
                baseline=baseline_metrics.primary_value,
                current=current_metrics.primary_value,
                delta=None,
                regressed=True,
                detail=(
                    f"primary metric changed: baseline measured "
                    f"{baseline_metrics.primary_metric!r}, current measures "
                    f"{current_metrics.primary_metric!r} — not comparable"
                ),
            )
        )
        any_regressed = True
    else:
        primary_regressed = primary_delta < -(th.primary_drop + th.tolerance)
        any_regressed = any_regressed or primary_regressed
        findings.append(
            RegressionFinding(
                signal="primary",
                baseline=baseline_metrics.primary_value,
                current=current_metrics.primary_value,
                delta=primary_delta,
                regressed=primary_regressed,
                detail=(
                    f"{metric_name} {baseline_metrics.primary_value:.4g} -> "
                    f"{current_metrics.primary_value:.4g} ({primary_delta:+.4g})"
                ),
            )
        )

    # --- error_rate (lower is better) ---------------------------------------
    error_delta = current_metrics.error_rate - baseline_metrics.error_rate
    error_regressed = error_delta > (th.error_rate_rise + th.tolerance)
    any_regressed = any_regressed or error_regressed
    findings.append(
        RegressionFinding(
            signal="error_rate",
            baseline=baseline_metrics.error_rate,
            current=current_metrics.error_rate,
            delta=error_delta,
            regressed=error_regressed,
            detail=(
                f"error_rate {baseline_metrics.error_rate:.4g} -> "
                f"{current_metrics.error_rate:.4g} ({error_delta:+.4g})"
            ),
        )
    )

    # --- per-class F1 (higher is better) ------------------------------------
    base_f1 = baseline_metrics.per_class_f1
    cur_f1 = current_metrics.per_class_f1
    for label in sorted(set(base_f1) | set(cur_f1)):
        b = base_f1.get(label)
        c = cur_f1.get(label)
        if b is None or c is None:
            # Class appeared or vanished — informational, not a hard fail.
            which = "appeared" if b is None else "vanished"
            findings.append(
                RegressionFinding(
                    signal=f"f1[{label}]",
                    baseline=b,
                    current=c,
                    delta=None,
                    regressed=False,
                    detail=f"class {label!r} {which} between baseline and current",
                )
            )
            continue
        delta = c - b
        regressed = delta < -(th.per_class_f1_drop + th.tolerance)
        any_regressed = any_regressed or regressed
        findings.append(
            RegressionFinding(
                signal=f"f1[{label}]",
                baseline=b,
                current=c,
                delta=delta,
                regressed=regressed,
                detail=f"F1[{label}] {b:.4g} -> {c:.4g} ({delta:+.4g})",
            )
        )

    return RegressionResult(regressed=any_regressed, findings=findings)
