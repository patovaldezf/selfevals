"""Pure no-regression gate over two `IterationMetrics` snapshots.

This is the math a CI step runs: given a *baseline* iteration (the versioned
"best so far" of an experiment) and the *current* iteration, decide whether
quality regressed past a configurable threshold. The function is deliberately
free of storage and CLI concerns — it takes two `IterationMetrics` and a
`RegressionThresholds`, and returns a `RegressionResult` the CLI turns into an
exit code. Keeping it pure means the whole decision is unit-testable without a
database.

What it compares (a *drop* is `baseline - current`, so positive == got worse):

- **primary metric** (pass@1 / whatever `metrics.primary` is): a drop greater
  than `max_primary_drop` is a regression.
- **per-class F1** from the confusion matrix (`metrics.confusion`): for every
  class present in *both* sides, a drop greater than `max_f1_drop` is a
  regression. The macro-F1 is checked the same way. A class present on only one
  side can't be compared, so it's reported as an informational finding, never a
  failure (we don't want a relabeling to masquerade as a regression).
- **error_rate** (optional): a *rise* greater than `max_error_rate_rise` is, by
  default, only a warning — a CI that wants it to fail the build sets
  `error_rate_is_failure=True`.

`RegressionResult.regressed` is the single boolean the gate hangs on: it's
`True` iff at least one *failing* finding exists. Warnings (and informational
class-mismatch notes) never set it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from selfevals.graders._confusion import ConfusionReport
from selfevals.schemas.iteration import IterationMetrics

# Float slack so a drop *exactly* at the threshold (e.g. 0.90 - 0.85, which is
# 0.05000000000000004 in IEEE-754) is not spuriously flagged as over it.
_EPSILON = 1e-9


class FindingSeverity(StrEnum):
    """How a single finding affects the gate verdict."""

    FAIL = "fail"
    """A real regression — sets `RegressionResult.regressed`."""
    WARN = "warn"
    """Worse but under policy to fail — surfaced, never fails the gate."""
    INFO = "info"
    """Can't be compared (e.g. a class on only one side) — purely informational."""


@dataclass(frozen=True)
class RegressionThresholds:
    """Configurable tolerances for the gate.

    Each `max_*_drop` is the largest *worsening* tolerated before a metric is
    flagged. Equality with the threshold is allowed (a drop *exactly* at the
    threshold passes); only a strictly larger drop fails — so a threshold of
    `0.0` means "any worsening at all is a regression".
    """

    max_primary_drop: float = 0.05
    max_f1_drop: float = 0.05
    max_error_rate_rise: float = 0.05
    error_rate_is_failure: bool = False
    """When True, an error_rate rise past `max_error_rate_rise` FAILs the gate
    instead of merely warning."""


@dataclass(frozen=True)
class RegressionFinding:
    """One observation about a single metric, baseline vs current."""

    metric: str
    """Stable identifier, e.g. 'primary', 'macro_f1', 'f1[refund]', 'error_rate'."""
    severity: FindingSeverity
    baseline: float | None
    current: float | None
    drop: float | None
    """`baseline - current` for "higher is better" metrics (primary, F1);
    `current - baseline` for "lower is better" metrics (error_rate). `None`
    when one side is missing so a numeric drop is undefined."""
    threshold: float | None
    message: str

    @property
    def is_failure(self) -> bool:
        return self.severity is FindingSeverity.FAIL


@dataclass(frozen=True)
class RegressionResult:
    """The gate verdict: did quality regress, and why."""

    findings: list[RegressionFinding] = field(default_factory=list)

    @property
    def regressed(self) -> bool:
        """True iff any finding is a failure. This is the CI exit-code signal."""
        return any(f.is_failure for f in self.findings)

    @property
    def failures(self) -> list[RegressionFinding]:
        return [f for f in self.findings if f.severity is FindingSeverity.FAIL]

    @property
    def warnings(self) -> list[RegressionFinding]:
        return [f for f in self.findings if f.severity is FindingSeverity.WARN]


def _primary_finding(
    baseline: IterationMetrics,
    current: IterationMetrics,
    thresholds: RegressionThresholds,
) -> RegressionFinding:
    base = baseline.primary.value
    cur = current.primary.value
    drop = base - cur
    name = current.primary.name
    if drop > thresholds.max_primary_drop + _EPSILON:
        return RegressionFinding(
            metric="primary",
            severity=FindingSeverity.FAIL,
            baseline=base,
            current=cur,
            drop=drop,
            threshold=thresholds.max_primary_drop,
            message=(
                f"primary metric {name!r} dropped {drop:.4g} "
                f"({base:.4g} -> {cur:.4g}), over threshold {thresholds.max_primary_drop:.4g}"
            ),
        )
    return RegressionFinding(
        metric="primary",
        severity=FindingSeverity.INFO,
        baseline=base,
        current=cur,
        drop=drop,
        threshold=thresholds.max_primary_drop,
        message=(
            f"primary metric {name!r} {base:.4g} -> {cur:.4g} "
            f"(drop {drop:.4g} within threshold {thresholds.max_primary_drop:.4g})"
        ),
    )


def _f1_findings(
    baseline: IterationMetrics,
    current: IterationMetrics,
    thresholds: RegressionThresholds,
) -> list[RegressionFinding]:
    """Per-class + macro F1 comparison from the persisted confusion matrices.

    When either side has no confusion matrix there is nothing to compare; we
    emit a single INFO note so the report is honest about *why* F1 wasn't
    checked rather than silently passing.
    """
    if baseline.confusion is None or current.confusion is None:
        which = []
        if baseline.confusion is None:
            which.append("baseline")
        if current.confusion is None:
            which.append("current")
        return [
            RegressionFinding(
                metric="confusion",
                severity=FindingSeverity.INFO,
                baseline=None,
                current=None,
                drop=None,
                threshold=None,
                message=(
                    f"no confusion matrix on {' and '.join(which)}; "
                    "per-class F1 not compared"
                ),
            )
        ]

    base_report = ConfusionReport.from_dict(baseline.confusion)
    cur_report = ConfusionReport.from_dict(current.confusion)
    findings: list[RegressionFinding] = []

    base_f1 = base_report.per_label_f1
    cur_f1 = cur_report.per_label_f1
    for label in sorted(set(base_f1) | set(cur_f1)):
        b = base_f1.get(label)
        c = cur_f1.get(label)
        findings.append(_compare_f1(f"f1[{label}]", b, c, thresholds.max_f1_drop))

    findings.append(
        _compare_f1("macro_f1", base_report.macro_f1, cur_report.macro_f1, thresholds.max_f1_drop)
    )
    return findings


def _compare_f1(
    metric: str,
    base: float | None,
    cur: float | None,
    max_drop: float,
) -> RegressionFinding:
    """One F1 comparison. `None` on either side is uncomparable (INFO)."""
    if base is None or cur is None:
        return RegressionFinding(
            metric=metric,
            severity=FindingSeverity.INFO,
            baseline=base,
            current=cur,
            drop=None,
            threshold=max_drop,
            message=(
                f"{metric} not comparable "
                f"(baseline={_fmt(base)}, current={_fmt(cur)})"
            ),
        )
    drop = base - cur
    if drop > max_drop + _EPSILON:
        return RegressionFinding(
            metric=metric,
            severity=FindingSeverity.FAIL,
            baseline=base,
            current=cur,
            drop=drop,
            threshold=max_drop,
            message=(
                f"{metric} dropped {drop:.4g} ({base:.4g} -> {cur:.4g}), "
                f"over threshold {max_drop:.4g}"
            ),
        )
    return RegressionFinding(
        metric=metric,
        severity=FindingSeverity.INFO,
        baseline=base,
        current=cur,
        drop=drop,
        threshold=max_drop,
        message=(
            f"{metric} {base:.4g} -> {cur:.4g} "
            f"(drop {drop:.4g} within threshold {max_drop:.4g})"
        ),
    )


def _error_rate_finding(
    baseline: IterationMetrics,
    current: IterationMetrics,
    thresholds: RegressionThresholds,
) -> RegressionFinding:
    base = baseline.error_rate
    cur = current.error_rate
    rise = cur - base  # lower is better, so a positive rise is worsening
    if rise > thresholds.max_error_rate_rise + _EPSILON:
        severity = (
            FindingSeverity.FAIL if thresholds.error_rate_is_failure else FindingSeverity.WARN
        )
        verb = "FAIL" if thresholds.error_rate_is_failure else "warn"
        return RegressionFinding(
            metric="error_rate",
            severity=severity,
            baseline=base,
            current=cur,
            drop=rise,
            threshold=thresholds.max_error_rate_rise,
            message=(
                f"error_rate rose {rise:.4g} ({base:.4g} -> {cur:.4g}), "
                f"over threshold {thresholds.max_error_rate_rise:.4g} [{verb}]"
            ),
        )
    return RegressionFinding(
        metric="error_rate",
        severity=FindingSeverity.INFO,
        baseline=base,
        current=cur,
        drop=rise,
        threshold=thresholds.max_error_rate_rise,
        message=(
            f"error_rate {base:.4g} -> {cur:.4g} "
            f"(rise {rise:.4g} within threshold {thresholds.max_error_rate_rise:.4g})"
        ),
    )


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4g}"


def evaluate_regression(
    baseline: IterationMetrics,
    current: IterationMetrics,
    thresholds: RegressionThresholds | None = None,
) -> RegressionResult:
    """Compare `current` against `baseline` and return the gate verdict.

    Pure: no storage, no I/O. `baseline` is the versioned best of the
    experiment; `current` is the iteration under test. The result's
    `regressed` property is the single boolean a CI gate exits on.
    """
    thresholds = thresholds or RegressionThresholds()
    findings: list[RegressionFinding] = []
    findings.append(_primary_finding(baseline, current, thresholds))
    findings.extend(_f1_findings(baseline, current, thresholds))
    findings.append(_error_rate_finding(baseline, current, thresholds))
    return RegressionResult(findings=findings)
