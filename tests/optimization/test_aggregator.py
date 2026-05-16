from __future__ import annotations

import pytest

from bootstrap.graders.base import GradeLabel
from bootstrap.optimization.aggregator import CaseOutcome, aggregate_iteration


def _outcome(labels: list[GradeLabel], *, cost: float = 0.0, duration: int = 0,
             failure_modes: list[str] | None = None) -> CaseOutcome:
    return CaseOutcome(
        case_id=f"ec_{hash(tuple(labels))}",
        per_repetition_label=labels,
        per_repetition_score=[
            1.0 if label == GradeLabel.PASS else 0.0 for label in labels
        ],
        cost_usd=cost,
        duration_ms=duration,
        failure_modes=failure_modes or [],
    )


def test_empty_outcomes_returns_zero() -> None:
    agg = aggregate_iteration(case_outcomes=[])
    assert agg.primary_value == 0.0
    assert agg.case_count == 0


def test_pass_at_1_is_default_metric() -> None:
    outcomes = [
        _outcome([GradeLabel.PASS]),
        _outcome([GradeLabel.FAIL]),
        _outcome([GradeLabel.PASS]),
        _outcome([GradeLabel.PASS]),
    ]
    agg = aggregate_iteration(case_outcomes=outcomes)
    assert agg.primary_metric == "pass@1"
    assert agg.primary_value == 0.75


def test_pass_at_k_counts_any_in_first_k() -> None:
    outcomes = [
        _outcome([GradeLabel.FAIL, GradeLabel.PASS, GradeLabel.PASS]),  # rep 1 passes
        _outcome([GradeLabel.FAIL, GradeLabel.FAIL, GradeLabel.FAIL]),  # never passes
        _outcome([GradeLabel.PASS, GradeLabel.PASS, GradeLabel.PASS]),  # always passes
    ]
    agg = aggregate_iteration(
        case_outcomes=outcomes,
        primary_metric="pass@1",
        reliability_metrics=["pass@2", "pass@3"],
    )
    # pass@1 = first rep passes (1/3 = 0.333)
    assert pytest.approx(agg.primary_value, abs=1e-9) == 1 / 3
    # pass@2 = any of first 2 reps passes (2/3 = 0.666)
    assert pytest.approx(agg.reliability["pass@2"], abs=1e-9) == 2 / 3
    # pass@3 = any of first 3 reps passes (2/3 = 0.666)
    assert pytest.approx(agg.reliability["pass@3"], abs=1e-9) == 2 / 3


def test_pass_caret_k_requires_all_passes() -> None:
    outcomes = [
        _outcome([GradeLabel.PASS, GradeLabel.PASS, GradeLabel.PASS]),  # 3/3 → pass^3
        _outcome([GradeLabel.PASS, GradeLabel.PASS, GradeLabel.FAIL]),  # 2/3 → not pass^3
    ]
    agg = aggregate_iteration(
        case_outcomes=outcomes,
        primary_metric="pass^3",
    )
    assert agg.primary_value == 0.5


def test_consistency_rate() -> None:
    outcomes = [
        _outcome([GradeLabel.PASS, GradeLabel.PASS]),  # 1.0
        _outcome([GradeLabel.PASS, GradeLabel.FAIL]),  # 0.5
        _outcome([GradeLabel.FAIL, GradeLabel.FAIL]),  # 0.0
    ]
    agg = aggregate_iteration(
        case_outcomes=outcomes,
        reliability_metrics=["consistency_rate"],
    )
    assert pytest.approx(agg.reliability["consistency_rate"], abs=1e-9) == 0.5


def test_recovery_rate_only_counts_failed_first_then_passed() -> None:
    outcomes = [
        _outcome([GradeLabel.FAIL, GradeLabel.PASS]),  # recovered
        _outcome([GradeLabel.FAIL, GradeLabel.FAIL]),  # never recovered
        _outcome([GradeLabel.PASS, GradeLabel.PASS]),  # already passed (excluded)
    ]
    agg = aggregate_iteration(
        case_outcomes=outcomes,
        reliability_metrics=["recovery_rate"],
    )
    assert agg.reliability["recovery_rate"] == 0.5  # 1 of 2 cases that initially failed


def test_failure_mode_counts_aggregated() -> None:
    outcomes = [
        _outcome([GradeLabel.FAIL], failure_modes=["missing_required_tool", "forbidden_substring"]),
        _outcome([GradeLabel.FAIL], failure_modes=["missing_required_tool"]),
        _outcome([GradeLabel.PASS]),
    ]
    agg = aggregate_iteration(case_outcomes=outcomes)
    assert agg.failure_mode_counts == {
        "missing_required_tool": 2,
        "forbidden_substring": 1,
    }


def test_guardrails_emitted_when_cost_or_duration_observed() -> None:
    outcomes = [
        _outcome([GradeLabel.PASS], cost=0.05, duration=2000),
        _outcome([GradeLabel.PASS], cost=0.10, duration=1000),
    ]
    agg = aggregate_iteration(case_outcomes=outcomes)
    assert agg.guardrails["cost_usd_per_case"] == pytest.approx(0.075)
    assert agg.guardrails["latency_ms_per_case_avg"] == pytest.approx(1500.0)


def test_unsupported_metric_raises() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        aggregate_iteration(
            case_outcomes=[_outcome([GradeLabel.PASS])],
            primary_metric="my_made_up_metric",
        )
