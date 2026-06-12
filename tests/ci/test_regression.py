"""Unit tests for the pure regression gate (`selfevals.ci.regression`)."""

from __future__ import annotations

from selfevals.ci.regression import (
    FindingSeverity,
    RegressionThresholds,
    evaluate_regression,
)
from selfevals.graders._confusion import confusion_from_pairs
from selfevals.schemas.iteration import IterationMetrics, MetricObservation


def _metrics(
    *,
    primary: float,
    primary_name: str = "pass@1",
    error_rate: float = 0.0,
    confusion: dict[str, object] | None = None,
) -> IterationMetrics:
    return IterationMetrics(
        primary=MetricObservation(name=primary_name, value=primary),
        error_rate=error_rate,
        confusion=confusion,
    )


def _confusion(pairs: list[tuple[str, str]]) -> dict[str, object]:
    return confusion_from_pairs(pairs).to_dict()


def test_no_regression_when_metrics_hold() -> None:
    base = _metrics(primary=0.8)
    cur = _metrics(primary=0.8)
    result = evaluate_regression(base, cur)
    assert not result.regressed
    assert result.failures == []


def test_no_regression_when_current_improves() -> None:
    base = _metrics(primary=0.6)
    cur = _metrics(primary=0.9)
    result = evaluate_regression(base, cur)
    assert not result.regressed


def test_primary_drop_over_threshold_fails() -> None:
    base = _metrics(primary=0.90)
    cur = _metrics(primary=0.80)  # drop 0.10 > default 0.05
    result = evaluate_regression(base, cur)
    assert result.regressed
    fails = result.failures
    assert len(fails) == 1
    assert fails[0].metric == "primary"
    assert fails[0].drop is not None
    assert abs(fails[0].drop - 0.10) < 1e-9


def test_primary_drop_within_threshold_passes() -> None:
    base = _metrics(primary=0.90)
    cur = _metrics(primary=0.86)  # drop 0.04 < 0.05
    result = evaluate_regression(base, cur)
    assert not result.regressed


def test_primary_drop_exactly_at_threshold_passes() -> None:
    """Equality with the threshold passes — only a strictly larger drop fails."""
    base = _metrics(primary=0.90)
    cur = _metrics(primary=0.85)  # drop exactly 0.05
    result = evaluate_regression(base, cur, RegressionThresholds(max_primary_drop=0.05))
    assert not result.regressed


def test_zero_threshold_fails_on_any_drop() -> None:
    base = _metrics(primary=0.90)
    cur = _metrics(primary=0.899)
    result = evaluate_regression(base, cur, RegressionThresholds(max_primary_drop=0.0))
    assert result.regressed


def test_per_class_f1_drop_fails() -> None:
    # Baseline: perfect on both classes (F1[b] = 1.0).
    base_pairs = [("a", "a"), ("a", "a"), ("b", "b"), ("b", "b")]
    # Current: some 'b' actuals now predicted 'a', so F1[b] drops to ~0.67 while
    # remaining defined on both sides (a comparable per-class drop).
    cur_pairs = [("a", "a"), ("a", "a"), ("b", "b"), ("b", "a")]
    base = _metrics(primary=0.8, confusion=_confusion(base_pairs))
    cur = _metrics(primary=0.8, confusion=_confusion(cur_pairs))
    result = evaluate_regression(base, cur)
    assert result.regressed
    failed_metrics = {f.metric for f in result.failures}
    assert "f1[b]" in failed_metrics


def test_macro_f1_drop_fails() -> None:
    # Baseline perfect; current keeps both classes defined but worse, so
    # macro_f1 stays a real number yet drops past threshold.
    base_pairs = [("a", "a"), ("a", "a"), ("b", "b"), ("b", "b")]
    cur_pairs = [("a", "a"), ("b", "a"), ("b", "b"), ("a", "b")]  # mixed errors
    base = _metrics(primary=0.5, confusion=_confusion(base_pairs))
    cur = _metrics(primary=0.5, confusion=_confusion(cur_pairs))
    result = evaluate_regression(base, cur)
    assert result.regressed
    assert any(f.metric == "macro_f1" for f in result.failures)


def test_per_class_f1_stable_passes() -> None:
    pairs = [("a", "a"), ("a", "a"), ("b", "b"), ("b", "b")]
    base = _metrics(primary=0.8, confusion=_confusion(pairs))
    cur = _metrics(primary=0.8, confusion=_confusion(pairs))
    result = evaluate_regression(base, cur)
    assert not result.regressed


def test_confusion_none_on_both_sides_is_info_not_failure() -> None:
    base = _metrics(primary=0.8, confusion=None)
    cur = _metrics(primary=0.8, confusion=None)
    result = evaluate_regression(base, cur)
    assert not result.regressed
    confusion_findings = [f for f in result.findings if f.metric == "confusion"]
    assert len(confusion_findings) == 1
    assert confusion_findings[0].severity is FindingSeverity.INFO


def test_confusion_none_on_one_side_is_info_not_failure() -> None:
    base = _metrics(primary=0.8, confusion=_confusion([("a", "a"), ("b", "b")]))
    cur = _metrics(primary=0.8, confusion=None)
    result = evaluate_regression(base, cur)
    assert not result.regressed
    confusion_findings = [f for f in result.findings if f.metric == "confusion"]
    assert len(confusion_findings) == 1
    assert "current" in confusion_findings[0].message


def test_class_absent_on_one_side_is_info_not_failure() -> None:
    """A class present only in one matrix can't be compared — info, never fail."""
    base = _metrics(primary=0.8, confusion=_confusion([("a", "a"), ("b", "b")]))
    # 'c' appears only in current; its per-class F1 has no baseline counterpart.
    cur = _metrics(
        primary=0.8, confusion=_confusion([("a", "a"), ("b", "b"), ("c", "c")])
    )
    result = evaluate_regression(base, cur)
    assert not result.regressed
    c_findings = [f for f in result.findings if f.metric == "f1[c]"]
    assert len(c_findings) == 1
    assert c_findings[0].severity is FindingSeverity.INFO


def test_error_rate_rise_warns_by_default() -> None:
    base = _metrics(primary=0.8, error_rate=0.0)
    cur = _metrics(primary=0.8, error_rate=0.20)  # rise 0.20 > 0.05
    result = evaluate_regression(base, cur)
    assert not result.regressed  # warn, not fail
    assert len(result.warnings) == 1
    assert result.warnings[0].metric == "error_rate"


def test_error_rate_rise_fails_when_configured() -> None:
    base = _metrics(primary=0.8, error_rate=0.0)
    cur = _metrics(primary=0.8, error_rate=0.20)
    result = evaluate_regression(
        base, cur, RegressionThresholds(error_rate_is_failure=True)
    )
    assert result.regressed
    assert any(f.metric == "error_rate" for f in result.failures)


def test_error_rate_within_threshold_is_info() -> None:
    base = _metrics(primary=0.8, error_rate=0.0)
    cur = _metrics(primary=0.8, error_rate=0.03)  # rise 0.03 < 0.05
    result = evaluate_regression(base, cur)
    assert not result.regressed
    assert result.warnings == []


def test_multiple_failures_all_reported() -> None:
    base_pairs = [("a", "a"), ("a", "a"), ("b", "b"), ("b", "b")]
    cur_pairs = [("a", "a"), ("a", "a"), ("b", "b"), ("b", "a")]  # F1[b] drops
    base = _metrics(primary=0.90, confusion=_confusion(base_pairs))
    cur = _metrics(primary=0.70, confusion=_confusion(cur_pairs))  # primary drops
    result = evaluate_regression(base, cur)
    assert result.regressed
    failed_metrics = {f.metric for f in result.failures}
    assert "primary" in failed_metrics
    assert "f1[b]" in failed_metrics
