from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from selfevals.graders.base import BreakdownNode, GradeLabel
from selfevals.optimization.aggregator import (
    Aggregator,
    CaseOutcome,
    FunnelNode,
    aggregate_iteration,
)
from selfevals.runner.executor import CaseRun, RepetitionResult
from selfevals.schemas.enums import (
    DatasetSource,
    DatasetType,
    GroundTruthMethod,
    Level,
    SandboxMode,
)
from selfevals.schemas.eval_case import (
    CaseTaxonomy,
    EvalCase,
    Expected,
    FeatureTag,
    GroundTruthSpec,
    SourceInfo,
)
from selfevals.schemas.trace import AgentSnapshotRef, RunInfo, Trace
from selfevals.storage.filesystem import FilesystemObjectStore
from selfevals.trace.payload_router import PayloadRouter
from selfevals.trace.recorder import TraceRecorder

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"
T0 = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)


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


def _eval_case() -> EvalCase:
    return EvalCase(
        id=EvalCase.make_id(),
        workspace_id=WS,
        name="t",
        task_type="x",
        input={"messages": [{"role": "user", "content": "hi"}]},
        taxonomy=CaseTaxonomy(
            level=Level.FINAL_RESPONSE,
            feature=FeatureTag(primary="commerce.product_resolution"),
            source=SourceInfo(type=DatasetSource.HANDCRAFTED),
            ground_truth=GroundTruthSpec(methods=[GroundTruthMethod.EXACT_MATCH]),
            dataset_type=DatasetType.CAPABILITY,
        ),
        expected=Expected(must_include=["pong"]),
    )


def _trace_with_llm_calls(tmp_path: Path, *, cache_hits: list[bool]) -> Trace:
    """Build a valid trace whose LLM call spans have the given cache_hit flags."""
    recorder = TraceRecorder(
        workspace_id=WS,
        run=RunInfo(run_id="run_01"),
        agent=AgentSnapshotRef(agent_id="ag_x", agent_version=1),
        framework_version="selfevals/0.0.3",
        runtime="python-3.12",
        sandbox=SandboxMode.MOCK,
        environment_started_at=T0,
        payload_router=PayloadRouter(FilesystemObjectStore(tmp_path), workspace_id=WS),
    )
    with recorder:
        for i, hit in enumerate(cache_hits):
            with recorder.llm_call(
                f"call-{i}", provider="anthropic", model="claude-sonnet-4-6"
            ) as llm:
                llm.cache_hit = hit
    return recorder.build()


def test_outcome_counts_cache_hits_across_traces(tmp_path: Path) -> None:
    # Two repetitions: rep 0 has 2 calls (1 cached), rep 1 has 1 call (cached).
    case_run = CaseRun(
        case_id="ec_x",
        repetitions=[
            RepetitionResult(
                repetition=0,
                trace=_trace_with_llm_calls(tmp_path / "r0", cache_hits=[True, False]),
                response=None,
                error=None,
            ),
            RepetitionResult(
                repetition=1,
                trace=_trace_with_llm_calls(tmp_path / "r1", cache_hits=[True]),
                response=None,
                error=None,
            ),
        ],
    )
    outcome = Aggregator.case_outcome(_eval_case(), case_run, [[], []])
    assert outcome.llm_call_count == 3
    assert outcome.cache_hit_count == 2


def test_aggregate_sums_cache_hits_and_llm_calls() -> None:
    outcomes = [
        CaseOutcome(
            case_id="a",
            per_repetition_label=[GradeLabel.PASS],
            per_repetition_score=[1.0],
            cache_hit_count=2,
            llm_call_count=3,
        ),
        CaseOutcome(
            case_id="b",
            per_repetition_label=[GradeLabel.PASS],
            per_repetition_score=[1.0],
            cache_hit_count=1,
            llm_call_count=4,
        ),
    ]
    agg = aggregate_iteration(case_outcomes=outcomes)
    assert agg.cache_hit_count == 3
    assert agg.llm_call_count == 7


def test_aggregate_cache_counts_default_to_zero() -> None:
    agg = aggregate_iteration(case_outcomes=[_outcome([GradeLabel.PASS])])
    assert agg.cache_hit_count == 0
    assert agg.llm_call_count == 0


def _grader_outcome(per_grader: dict[str, list[GradeLabel]]) -> CaseOutcome:
    """A CaseOutcome carrying per-grader labels, with the worst-of collapse
    computed the same way the live aggregator does (severity max per rep)."""
    severity = {
        GradeLabel.ERROR: 4,
        GradeLabel.FAIL: 3,
        GradeLabel.PARTIAL: 2,
        GradeLabel.SKIPPED: 1,
        GradeLabel.PASS: 0,
    }
    n_reps = max(len(v) for v in per_grader.values())
    collapsed: list[GradeLabel] = []
    for i in range(n_reps):
        rep_labels = [v[i] for v in per_grader.values() if i < len(v)]
        collapsed.append(max(rep_labels, key=lambda lbl: severity[lbl]))
    return CaseOutcome(
        case_id=f"ec_{hash(tuple(sorted(per_grader)))}",
        per_repetition_label=collapsed,
        per_repetition_score=[1.0 if lbl == GradeLabel.PASS else 0.0 for lbl in collapsed],
        per_grader_label=per_grader,
    )


def test_per_grader_pass_rate_unmasks_worst_of() -> None:
    # must_include passes on both cases; format fails one. Worst-of pass@1 reads
    # 0.5, but the per-grader breakdown shows must_include=1.0, format=0.5.
    outcomes = [
        _grader_outcome({"must_include": [GradeLabel.PASS], "format": [GradeLabel.PASS]}),
        _grader_outcome({"must_include": [GradeLabel.PASS], "format": [GradeLabel.FAIL]}),
    ]
    agg = aggregate_iteration(case_outcomes=outcomes)
    assert agg.primary_value == 0.5  # worst-of: one case fails because format failed
    assert agg.per_grader_pass_rate["must_include"] == pytest.approx(1.0)
    assert agg.per_grader_pass_rate["format"] == pytest.approx(0.5)


def test_primary_grader_scores_against_one_grader() -> None:
    outcomes = [
        _grader_outcome({"must_include": [GradeLabel.PASS], "format": [GradeLabel.PASS]}),
        _grader_outcome({"must_include": [GradeLabel.PASS], "format": [GradeLabel.FAIL]}),
    ]
    # Optimizing toward must_include: both cases pass it → primary 1.0, not 0.5.
    agg = aggregate_iteration(case_outcomes=outcomes, primary_grader="must_include")
    assert agg.primary_value == pytest.approx(1.0)
    assert agg.primary_grader == "must_include"
    # The masked per-grader signal is still reported alongside.
    assert agg.per_grader_pass_rate["format"] == pytest.approx(0.5)


def test_primary_grader_denominator_excludes_cases_it_didnt_run() -> None:
    # Grader "format" only ran on the second case; scoring against it must use a
    # denominator of 1 (the case it graded), not 2.
    outcomes = [
        _grader_outcome({"must_include": [GradeLabel.FAIL]}),
        _grader_outcome({"must_include": [GradeLabel.PASS], "format": [GradeLabel.PASS]}),
    ]
    agg = aggregate_iteration(case_outcomes=outcomes, primary_grader="format")
    assert agg.primary_value == pytest.approx(1.0)  # 1 of 1 case format ran on


def test_per_grader_pass_rate_empty_without_per_grader_labels() -> None:
    # Outcomes built without per-grader labels (e.g. rehydrated from metrics)
    # yield an empty per_grader_pass_rate rather than fabricating one.
    agg = aggregate_iteration(case_outcomes=[_outcome([GradeLabel.PASS])])
    assert agg.per_grader_pass_rate == {}
    assert agg.primary_grader is None


def test_funnel_node_dict_round_trips() -> None:
    # `from_dict` is the inverse of `to_dict`, including the nested subtree —
    # this is what lets a result reconstructed from storage carry the same
    # funnel a live run produced.
    node = FunnelNode(
        key="root",
        count=4,
        mean_score=0.75,
        total_weight=4.0,
        label_counts={"pass": 3, "fail": 1},
        failure_mode_counts={"fm_timeout": 1},
        children={
            "child": FunnelNode(key="child", count=2, mean_score=1.0, total_weight=2.0),
        },
    )
    restored = FunnelNode.from_dict(node.to_dict())
    assert restored == node


def test_funnel_node_from_dict_tolerates_missing_optionals() -> None:
    # A minimal persisted node (only `key`) rebuilds with the dataclass
    # defaults rather than raising.
    restored = FunnelNode.from_dict({"key": "k"})
    assert restored == FunnelNode(key="k")


def test_pass_at_1_excludes_errored_cases_from_denominator() -> None:
    # 3 cases: 1 PASS, 1 FAIL, 1 ERROR. The errored case is excluded from the
    # denominator, so pass@1 = 1 pass / 2 scored = 0.5 (not 1/3).
    outcomes = [
        _outcome([GradeLabel.PASS]),
        _outcome([GradeLabel.FAIL]),
        _outcome([GradeLabel.ERROR]),
    ]
    agg = aggregate_iteration(case_outcomes=outcomes)
    assert agg.primary_value == 0.5
    assert pytest.approx(agg.error_rate, abs=1e-9) == 1 / 3


def test_error_rate_zero_when_nothing_errored() -> None:
    outcomes = [_outcome([GradeLabel.PASS]), _outcome([GradeLabel.FAIL])]
    agg = aggregate_iteration(case_outcomes=outcomes)
    assert agg.error_rate == 0.0
    # pass@1 unchanged: no exclusions, 1/2 = 0.5.
    assert agg.primary_value == 0.5


def test_pass_at_1_all_errored_does_not_divide_by_zero() -> None:
    outcomes = [_outcome([GradeLabel.ERROR]), _outcome([GradeLabel.ERROR])]
    agg = aggregate_iteration(case_outcomes=outcomes)
    assert agg.primary_value == 0.0
    assert agg.error_rate == 1.0


def test_error_rate_propagated_and_pass_at_k_intact() -> None:
    # pass@k / pass^k still count errored cases in their denominator (they read
    # the whole window), so only worst-of pass@1 changed. Here the errored case
    # never passes, so pass@2 = 1/3, pass^2 = 1/3, but pass@1 excludes it → 1/2.
    outcomes = [
        _outcome([GradeLabel.PASS, GradeLabel.PASS]),
        _outcome([GradeLabel.FAIL, GradeLabel.FAIL]),
        _outcome([GradeLabel.ERROR, GradeLabel.ERROR]),
    ]
    agg = aggregate_iteration(
        case_outcomes=outcomes,
        primary_metric="pass@1",
        reliability_metrics=["pass@2", "pass^2"],
    )
    assert agg.primary_value == 0.5
    assert pytest.approx(agg.reliability["pass@2"], abs=1e-9) == 1 / 3
    assert pytest.approx(agg.reliability["pass^2"], abs=1e-9) == 1 / 3
    assert pytest.approx(agg.error_rate, abs=1e-9) == 1 / 3


def test_repetition_error_marks_outcome_errored_even_without_grade(tmp_path: Path) -> None:
    # A RepetitionResult.error makes rep 0's effective label ERROR even when the
    # grader produced no results, so the case is excluded from pass@1.
    case_run = CaseRun(
        case_id="ec_x",
        repetitions=[
            RepetitionResult(
                repetition=0,
                trace=_trace_with_llm_calls(tmp_path / "r0", cache_hits=[False]),
                response=None,
                error="adapter blew up",
            ),
        ],
    )
    outcome = Aggregator.case_outcome(_eval_case(), case_run, [[]])
    assert outcome.errored is True
    assert outcome.per_repetition_label[0] == GradeLabel.ERROR
    agg = aggregate_iteration(
        case_outcomes=[outcome, _outcome([GradeLabel.PASS])],
    )
    # Errored case excluded → 1 pass / 1 scored = 1.0; error_rate = 1/2.
    assert agg.primary_value == 1.0
    assert agg.error_rate == 0.5
