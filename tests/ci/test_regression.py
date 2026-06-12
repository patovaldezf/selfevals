"""Unit tests for the pure regression gate (`selfevals.ci.regression`).

No storage, no clock — just the math. These pin the gate's contract: which
deltas flip `regressed`, how per-class F1 and error_rate are weighed, and the
deadzone behavior around float noise.
"""

from __future__ import annotations

from selfevals.ci.regression import (
    BaselineMetrics,
    RegressionThresholds,
    evaluate_regression,
)


def _m(
    primary: float,
    *,
    metric: str = "pass@1",
    error_rate: float = 0.0,
    f1: dict[str, float] | None = None,
) -> BaselineMetrics:
    return BaselineMetrics(
        primary_metric=metric,
        primary_value=primary,
        error_rate=error_rate,
        per_class_f1=f1 or {},
    )


def test_no_change_is_not_a_regression() -> None:
    result = evaluate_regression(_m(0.8), _m(0.8))
    assert result.regressed is False
    assert "no regression" in result.summary()


def test_improvement_is_not_a_regression() -> None:
    result = evaluate_regression(_m(0.7), _m(0.9))
    assert result.regressed is False
    primary = next(f for f in result.findings if f.signal == "primary")
    assert primary.delta is not None and primary.delta > 0


def test_primary_drop_beyond_threshold_regresses() -> None:
    # Default primary_drop=0.0 → any real drop fails.
    result = evaluate_regression(_m(0.8), _m(0.75))
    assert result.regressed is True
    assert any(f.signal == "primary" and f.regressed for f in result.findings)


def test_primary_drop_within_tolerance_band_passes() -> None:
    th = RegressionThresholds(primary_drop=0.05)
    # A 3-point drop is allowed when the band is 5 points.
    result = evaluate_regression(_m(0.80), _m(0.77), th)
    assert result.regressed is False


def test_primary_drop_exactly_at_threshold_passes() -> None:
    th = RegressionThresholds(primary_drop=0.05)
    result = evaluate_regression(_m(0.80), _m(0.75), th)
    assert result.regressed is False  # exactly 0.05 drop is allowed, not >.


def test_float_noise_does_not_trip_the_gate() -> None:
    result = evaluate_regression(_m(0.8), _m(0.8 - 1e-12))
    assert result.regressed is False


def test_error_rate_rise_regresses() -> None:
    result = evaluate_regression(_m(0.8, error_rate=0.0), _m(0.8, error_rate=0.1))
    assert result.regressed is True
    err = next(f for f in result.findings if f.signal == "error_rate")
    assert err.regressed is True


def test_error_rate_drop_is_fine() -> None:
    result = evaluate_regression(_m(0.8, error_rate=0.2), _m(0.8, error_rate=0.05))
    assert result.regressed is False


def test_per_class_f1_drop_regresses_even_when_primary_holds() -> None:
    # Aggregate pass@1 unchanged, but one class's F1 collapsed.
    base = _m(0.8, f1={"refund": 0.9, "ship": 0.9})
    cur = _m(0.8, f1={"refund": 0.5, "ship": 0.9})
    result = evaluate_regression(base, cur)
    assert result.regressed is True
    refund = next(f for f in result.findings if f.signal == "f1[refund]")
    assert refund.regressed is True
    ship = next(f for f in result.findings if f.signal == "f1[ship]")
    assert ship.regressed is False


def test_per_class_f1_small_drop_within_threshold_passes() -> None:
    base = _m(0.8, f1={"refund": 0.90})
    cur = _m(0.8, f1={"refund": 0.87})  # 3-point drop, default band is 5.
    result = evaluate_regression(base, cur)
    assert result.regressed is False


def test_class_appeared_or_vanished_is_informational_not_a_fail() -> None:
    base = _m(0.8, f1={"refund": 0.9})
    cur = _m(0.8, f1={"refund": 0.9, "newclass": 0.4})
    result = evaluate_regression(base, cur)
    assert result.regressed is False
    appeared = next(f for f in result.findings if f.signal == "f1[newclass]")
    assert appeared.regressed is False
    assert "appeared" in appeared.detail


def test_changed_primary_metric_is_flagged_not_silently_passed() -> None:
    base = _m(0.8, metric="pass@1")
    cur = _m(0.8, metric="macro_f1")
    result = evaluate_regression(base, cur)
    assert result.regressed is True
    primary = next(f for f in result.findings if f.signal == "primary")
    assert "not comparable" in primary.detail


def test_from_confusion_extracts_per_label_f1_dropping_none() -> None:
    confusion = {
        "per_label_f1": {"refund": 0.9, "ship": None, "track": 0.7},
    }
    m = BaselineMetrics.from_confusion(
        primary_metric="pass@1", primary_value=0.8, confusion=confusion
    )
    assert m.per_class_f1 == {"refund": 0.9, "track": 0.7}  # 'ship' (None) dropped.


def test_from_confusion_handles_missing_confusion() -> None:
    m = BaselineMetrics.from_confusion(primary_metric="pass@1", primary_value=0.8, confusion=None)
    assert m.per_class_f1 == {}


def test_summary_counts_only_regressions() -> None:
    base = _m(0.8, error_rate=0.0, f1={"a": 0.9, "b": 0.9})
    cur = _m(0.7, error_rate=0.1, f1={"a": 0.5, "b": 0.9})  # primary + error + f1[a].
    result = evaluate_regression(base, cur)
    assert result.regressed is True
    assert len(result.regressions) == 3
    assert "3 signals" in result.summary()
