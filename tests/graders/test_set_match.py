from __future__ import annotations

from datetime import UTC, datetime

import pytest

from selfevals.graders.base import BreakdownNode, GradeLabel, GraderContext
from selfevals.graders.set_match import (
    FM_EXTRANEOUS,
    FM_MISSING,
    FM_NO_DETECTED,
    SetMatchGrader,
)
from selfevals.runner.adapters import AdapterResponse
from selfevals.schemas.enums import (
    DatasetSource,
    DatasetType,
    GroundTruthMethod,
    Level,
    SandboxMode,
    TraceState,
)
from selfevals.schemas.eval_case import (
    CaseTaxonomy,
    EvalCase,
    Expected,
    FeatureTag,
    GroundTruthSpec,
    SourceInfo,
)
from selfevals.schemas.trace import (
    AgentSnapshotRef,
    AgentTurnSpan,
    EnvironmentInfo,
    FinalState,
    RunInfo,
    Trace,
)

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"
T0 = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)


def _case(expected: Expected) -> EvalCase:
    return EvalCase(
        id=EvalCase.make_id(),
        workspace_id=WS,
        name="t",
        task_type="intention_detection",
        input={"messages": [{"role": "user", "content": "price and stock and quote"}]},
        taxonomy=CaseTaxonomy(
            level=Level.FINAL_RESPONSE,
            feature=FeatureTag(primary="intention.detect"),
            source=SourceInfo(type=DatasetSource.HANDCRAFTED),
            ground_truth=GroundTruthSpec(methods=[GroundTruthMethod.EXACT_MATCH]),
            dataset_type=DatasetType.CAPABILITY,
        ),
        expected=expected,
    )


def _trace() -> Trace:
    return Trace(
        id=Trace.make_id(),
        workspace_id=WS,
        run=RunInfo(run_id="run_01"),
        agent=AgentSnapshotRef(agent_id="ag_x", agent_version=1),
        environment=EnvironmentInfo(
            framework_version="selfevals/0.10.0",
            runtime="python-3.12",
            sandbox=SandboxMode.MOCK,
            started_at=T0,
        ),
        final_state=FinalState(status=TraceState.COMPLETED),
        spans=[AgentTurnSpan(id="sp_turn", name="t", started_at=T0)],
    )


def _ctx(case: EvalCase, structured: dict[str, object] | None) -> GraderContext:
    response = AdapterResponse(content=None, structured_output=structured)
    return GraderContext(case=case, trace=_trace(), response=response)


def _child(node: BreakdownNode | None, key: str) -> BreakdownNode:
    assert node is not None
    for child in node.children:
        if child.key == key:
            return child
    raise AssertionError(f"no breakdown child {key!r}")


@pytest.mark.asyncio
async def test_identical_sets_pass_with_f1_one() -> None:
    case = _case(Expected(must_include=["Price Check", "Inventory Check", "Quote Request"]))
    structured = {"detected": ["Price Check", "Inventory Check", "Quote Request"]}
    res = await SetMatchGrader().grade(_ctx(case, structured))
    assert res.label == GradeLabel.PASS
    assert res.score == pytest.approx(1.0)
    assert res.details["f1"] == pytest.approx(1.0)
    assert res.details["precision"] == pytest.approx(1.0)
    assert res.breakdown is not None
    assert res.breakdown.key == "set_match"
    assert _child(res.breakdown, "completeness").score == pytest.approx(1.0)
    assert _child(res.breakdown, "f1").score == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_two_of_three_fails_default_gating_with_missing_mode() -> None:
    case = _case(Expected(must_include=["Price Check", "Inventory Check", "Quote Request"]))
    structured = {"detected": ["Price Check", "Inventory Check"]}  # missing Quote Request
    res = await SetMatchGrader().grade(_ctx(case, structured))
    assert res.label == GradeLabel.FAIL
    assert res.details["completeness"] == pytest.approx(2 / 3)
    assert res.details["precision"] == pytest.approx(1.0)  # nothing invented
    assert FM_MISSING in res.failure_modes
    # the missing expected element shows up as a FAIL leaf under completeness
    completeness = _child(res.breakdown, "completeness")
    quote = _child(completeness, "quote request")  # case-folded by default
    assert quote.label == GradeLabel.FAIL
    assert FM_MISSING in quote.failure_modes


@pytest.mark.asyncio
async def test_extraneous_detection_lowers_precision() -> None:
    case = _case(Expected(must_include=["Price Check"]))
    structured = {"detected": ["Price Check", "Order Cancel"]}  # invented one
    res = await SetMatchGrader().grade(_ctx(case, structured))
    # completeness is 1.0 (all expected found) so default gating PASSes...
    assert res.label == GradeLabel.PASS
    assert res.details["precision"] == pytest.approx(0.5)
    # ...but the precision dimension still records the extraneous element
    precision = _child(res.breakdown, "precision")
    extra = _child(precision, "order cancel")
    assert extra.label == GradeLabel.FAIL
    assert FM_EXTRANEOUS in extra.failure_modes


@pytest.mark.asyncio
async def test_aliases_normalize_before_comparison() -> None:
    expected = Expected(
        must_include=["Price Check"],
        aliases={"price_check": "Price Check"},
    )
    case = _case(expected)
    res = await SetMatchGrader().grade(_ctx(case, {"detected": ["price_check"]}))
    assert res.label == GradeLabel.PASS
    assert res.details["completeness"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_without_aliases_distinct_strings_do_not_match() -> None:
    case = _case(Expected(must_include=["Price Check"]))  # no aliases
    res = await SetMatchGrader().grade(_ctx(case, {"detected": ["price_check"]}))
    # case-folding makes "price check" != "price_check" — the underscore differs
    assert res.label == GradeLabel.FAIL
    assert res.details["completeness"] == pytest.approx(0.0)
    assert FM_MISSING in res.failure_modes


@pytest.mark.asyncio
async def test_missing_detected_key_is_hard_fail() -> None:
    case = _case(Expected(must_include=["Price Check"]))
    res = await SetMatchGrader().grade(_ctx(case, {"something_else": []}))
    assert res.label == GradeLabel.FAIL
    assert res.failure_modes == [FM_NO_DETECTED]
    assert res.breakdown is None


@pytest.mark.asyncio
async def test_detected_not_a_list_is_hard_fail() -> None:
    case = _case(Expected(must_include=["Price Check"]))
    res = await SetMatchGrader().grade(_ctx(case, {"detected": "Price Check"}))
    assert res.label == GradeLabel.FAIL
    assert res.failure_modes == [FM_NO_DETECTED]


@pytest.mark.asyncio
async def test_no_structured_output_is_hard_fail() -> None:
    case = _case(Expected(must_include=["Price Check"]))
    res = await SetMatchGrader().grade(_ctx(case, None))
    assert res.label == GradeLabel.FAIL
    assert res.failure_modes == [FM_NO_DETECTED]


@pytest.mark.asyncio
async def test_f1_gating_with_threshold() -> None:
    case = _case(Expected(must_include=["a", "b", "c", "d"]))
    structured = {"detected": ["a", "b", "c"]}  # 3/4 -> recall .75, precision 1 -> f1 .857
    grader = SetMatchGrader(gating="f1", threshold=0.8)
    res = await grader.grade(_ctx(case, structured))
    assert res.label == GradeLabel.PASS
    assert res.score == pytest.approx(0.857, abs=1e-3)
    # tighten the bar above the achieved f1 -> FAIL
    strict = SetMatchGrader(gating="f1", threshold=0.9)
    res2 = await strict.grade(_ctx(case, structured))
    assert res2.label == GradeLabel.FAIL


@pytest.mark.asyncio
async def test_breakdown_roundtrips_through_dict() -> None:
    case = _case(Expected(must_include=["a", "b"]))
    res = await SetMatchGrader().grade(_ctx(case, {"detected": ["a"]}))
    assert res.breakdown is not None
    restored = BreakdownNode.from_dict(res.breakdown.to_dict())
    assert restored.key == "set_match"
    assert restored.score == pytest.approx(res.breakdown.score)
    assert {c.key for c in restored.children} == {"completeness", "precision", "recall", "f1"}


@pytest.mark.asyncio
async def test_invalid_gating_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="gating"):
        SetMatchGrader(gating="bogus")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_invalid_threshold_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="threshold"):
        SetMatchGrader(threshold=1.5)


@pytest.mark.asyncio
async def test_default_extract_reads_detected_key() -> None:
    # Regression guard: the default `extract="detected"` is byte-identical to
    # the historical hard-coded `structured_output["detected"]` lookup.
    case = _case(Expected(must_include=["a", "b"]))
    result = await SetMatchGrader().grade(_ctx(case, {"detected": ["a", "b"]}))
    assert result.label is GradeLabel.PASS
    assert result.details["completeness"] == 1.0


@pytest.mark.asyncio
async def test_extract_routes_to_custom_key() -> None:
    case = _case(Expected(must_include=["price", "stock"]))
    grader = SetMatchGrader(extract="intents")
    result = await grader.grade(_ctx(case, {"intents": ["price", "stock"]}))
    assert result.label is GradeLabel.PASS
    assert result.details["detected"] == ["price", "stock"]


@pytest.mark.asyncio
async def test_extract_projects_over_entity_list() -> None:
    # The seals contract: structured entities, set extracted via projection.
    case = _case(Expected(must_include=["abc-uuid", "def-uuid"]))
    grader = SetMatchGrader(extract="candidates[].id")
    structured = {
        "candidates": [{"id": "abc-uuid", "external_id": "00019"}, {"id": "def-uuid"}],
    }
    result = await grader.grade(_ctx(case, structured))
    assert result.label is GradeLabel.PASS


@pytest.mark.asyncio
async def test_extract_missing_path_is_no_detected_fail() -> None:
    case = _case(Expected(must_include=["a"]))
    grader = SetMatchGrader(extract="candidates[].id")
    result = await grader.grade(_ctx(case, {"other": 1}))
    assert result.label is GradeLabel.FAIL
    assert FM_NO_DETECTED in result.failure_modes


@pytest.mark.asyncio
async def test_invalid_extract_path_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="segment"):
        SetMatchGrader(extract="a..b")
