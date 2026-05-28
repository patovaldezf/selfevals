from __future__ import annotations

import pytest

from selfevals.graders.base import BreakdownNode, GradeLabel
from selfevals.optimization.aggregator import CaseOutcome, aggregate_iteration


def _outcome(
    labels: list[GradeLabel],
    *,
    cost: float = 0.0,
    duration: int = 0,
    failure_modes: list[str] | None = None,
    breakdowns: list[BreakdownNode] | None = None,
) -> CaseOutcome:
    return CaseOutcome(
        case_id=f"ec_{hash(tuple(labels))}",
        per_repetition_label=labels,
        per_repetition_score=[1.0 if label == GradeLabel.PASS else 0.0 for label in labels],
        cost_usd=cost,
        duration_ms=duration,
        failure_modes=failure_modes or [],
        breakdowns=breakdowns or [],
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
    # Sorted latencies [1000, 2000]; type-7 interpolation.
    assert agg.guardrails["latency_ms_p50"] == pytest.approx(1500.0)
    assert agg.guardrails["latency_ms_p95"] == pytest.approx(1950.0)
    assert agg.guardrails["latency_ms_p99"] == pytest.approx(1990.0)


def test_latency_percentiles_single_case_is_that_value() -> None:
    agg = aggregate_iteration(case_outcomes=[_outcome([GradeLabel.PASS], duration=1234)])
    assert agg.guardrails["latency_ms_p50"] == pytest.approx(1234.0)
    assert agg.guardrails["latency_ms_p95"] == pytest.approx(1234.0)
    assert agg.guardrails["latency_ms_p99"] == pytest.approx(1234.0)


def test_latency_percentiles_absent_when_no_duration() -> None:
    agg = aggregate_iteration(case_outcomes=[_outcome([GradeLabel.PASS])])
    assert "latency_ms_p95" not in agg.guardrails
    assert "latency_ms_per_case_avg" not in agg.guardrails


def test_funnel_empty_when_no_breakdowns() -> None:
    agg = aggregate_iteration(case_outcomes=[_outcome([GradeLabel.PASS])])
    assert agg.funnel == {}


def test_funnel_rolls_up_by_key_with_weighted_mean_score() -> None:
    outcomes = [
        _outcome(
            [GradeLabel.PASS],
            breakdowns=[BreakdownNode(key="answer", label=GradeLabel.PASS, score=1.0, weight=2.0)],
        ),
        _outcome(
            [GradeLabel.FAIL],
            breakdowns=[
                BreakdownNode(
                    key="answer",
                    label=GradeLabel.FAIL,
                    score=0.0,
                    weight=2.0,
                    failure_modes=["wrong_answer"],
                )
            ],
        ),
    ]
    agg = aggregate_iteration(case_outcomes=outcomes)
    node = agg.funnel["answer"]
    assert node.count == 2
    # weighted mean: (1.0*2 + 0.0*2) / (2 + 2) = 0.5
    assert node.mean_score == pytest.approx(0.5)
    assert node.total_weight == pytest.approx(4.0)
    assert node.label_counts == {"pass": 1, "fail": 1}
    assert node.failure_mode_counts == {"wrong_answer": 1}


def test_funnel_recurses_into_children() -> None:
    outcomes = [
        _outcome(
            [GradeLabel.PARTIAL],
            breakdowns=[
                BreakdownNode(
                    key="overall",
                    score=0.5,
                    weight=1.0,
                    children=[
                        BreakdownNode(key="retrieval", score=1.0, weight=1.0),
                        BreakdownNode(key="answer", score=0.0, weight=1.0),
                    ],
                )
            ],
        ),
        _outcome(
            [GradeLabel.PASS],
            breakdowns=[
                BreakdownNode(
                    key="overall",
                    score=1.0,
                    weight=1.0,
                    children=[
                        BreakdownNode(key="retrieval", score=1.0, weight=1.0),
                    ],
                )
            ],
        ),
    ]
    agg = aggregate_iteration(case_outcomes=outcomes)
    overall = agg.funnel["overall"]
    assert overall.count == 2
    assert overall.mean_score == pytest.approx(0.75)
    # retrieval appears in both breakdowns
    assert overall.children["retrieval"].count == 2
    assert overall.children["retrieval"].mean_score == pytest.approx(1.0)
    # answer only in the first
    assert overall.children["answer"].count == 1
    assert overall.children["answer"].mean_score == pytest.approx(0.0)


def test_funnel_advisory_weight_zero_node_excluded_from_mean() -> None:
    # A weight=0 node (diagnostic / advisory) still counts and tallies failure
    # modes, but must not contribute to the weighted mean score.
    outcomes = [
        _outcome(
            [GradeLabel.PASS],
            breakdowns=[
                BreakdownNode(key="scored", score=1.0, weight=1.0),
                BreakdownNode(
                    key="diag",
                    score=0.0,
                    weight=0.0,
                    failure_modes=["slow_path"],
                ),
            ],
        ),
    ]
    agg = aggregate_iteration(case_outcomes=outcomes)
    assert agg.funnel["scored"].mean_score == pytest.approx(1.0)
    # weight=0 node has no scored weight → mean_score is None, but it is counted
    diag = agg.funnel["diag"]
    assert diag.mean_score is None
    assert diag.count == 1
    assert diag.failure_mode_counts == {"slow_path": 1}


def test_funnel_node_to_dict_is_json_serializable() -> None:
    import json

    outcomes = [
        _outcome(
            [GradeLabel.PASS],
            breakdowns=[
                BreakdownNode(
                    key="overall",
                    score=0.8,
                    children=[BreakdownNode(key="sub", score=0.8)],
                )
            ],
        ),
    ]
    agg = aggregate_iteration(case_outcomes=outcomes)
    payload = {key: node.to_dict() for key, node in agg.funnel.items()}
    dumped = json.dumps(payload)
    assert "overall" in dumped
    assert payload["overall"]["children"]["sub"]["mean_score"] == pytest.approx(0.8)


def test_unsupported_metric_raises() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        aggregate_iteration(
            case_outcomes=[_outcome([GradeLabel.PASS])],
            primary_metric="my_made_up_metric",
        )
