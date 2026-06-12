from __future__ import annotations

from datetime import UTC, datetime

import pytest

from selfevals.graders.base import BreakdownNode, GradeLabel, GraderContext
from selfevals.graders.classification import (
    FM_NO_EXPECTED,
    FM_NO_PREDICTION,
    ClassificationGrader,
    cell_key,
    misclassified_mode,
    parse_cell_key,
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
        task_type="classification",
        input={"messages": [{"role": "user", "content": "classify this order"}]},
        taxonomy=CaseTaxonomy(
            level=Level.FINAL_RESPONSE,
            feature=FeatureTag(primary="commerce.order_classification"),
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
            framework_version="selfevals/0.11.0",
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


def _cell_child(node: BreakdownNode | None) -> BreakdownNode:
    assert node is not None
    assert node.key == "classification"
    assert len(node.children) == 1
    return node.children[0]


# ---- cell-key codec ---------------------------------------------------------


def test_cell_key_round_trip() -> None:
    assert parse_cell_key(cell_key("full_order", "refund")) == ("full_order", "refund")


def test_parse_cell_key_rejects_non_cell() -> None:
    assert parse_cell_key("misclassified:a->b") is None
    assert parse_cell_key("cell:noarrow") is None
    assert parse_cell_key("cell:->b") is None
    assert parse_cell_key("cell:a->") is None


# ---- happy path -------------------------------------------------------------


@pytest.mark.asyncio
async def test_match_passes_and_emits_diagonal_cell() -> None:
    case = _case(Expected(outcome="full_order"))
    res = await ClassificationGrader().grade(_ctx(case, {"label": "full_order"}))
    assert res.label == GradeLabel.PASS
    assert res.score == 1.0
    assert res.failure_modes == []
    assert res.details["predicted"] == "full_order"
    assert res.details["expected"] == "full_order"
    cell = _cell_child(res.breakdown)
    assert parse_cell_key(cell.key) == ("full_order", "full_order")
    assert cell.label == GradeLabel.PASS


@pytest.mark.asyncio
async def test_mismatch_fails_with_misclassified_mode_and_cell() -> None:
    case = _case(Expected(outcome="full_order"))
    res = await ClassificationGrader().grade(_ctx(case, {"label": "refund"}))
    assert res.label == GradeLabel.FAIL
    assert res.score == 0.0
    # off-diagonal failure mode: predicted=refund, expected=full_order.
    assert misclassified_mode("refund", "full_order") in res.failure_modes
    cell = _cell_child(res.breakdown)
    # cell key carries (expected, predicted) order.
    assert parse_cell_key(cell.key) == ("full_order", "refund")
    assert cell.label == GradeLabel.FAIL


@pytest.mark.asyncio
async def test_case_insensitive_by_default() -> None:
    case = _case(Expected(outcome="Full_Order"))
    res = await ClassificationGrader().grade(_ctx(case, {"label": "FULL_ORDER"}))
    assert res.label == GradeLabel.PASS


@pytest.mark.asyncio
async def test_case_sensitive_when_requested() -> None:
    case = _case(Expected(outcome="Full_Order"))
    res = await ClassificationGrader(case_sensitive=True).grade(
        _ctx(case, {"label": "full_order"})
    )
    assert res.label == GradeLabel.FAIL


# ---- path selector ----------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_nested_path() -> None:
    case = _case(Expected(outcome="electronics"))
    grader = ClassificationGrader(extract="result.category")
    res = await grader.grade(_ctx(case, {"result": {"category": "electronics"}}))
    assert res.label == GradeLabel.PASS
    assert res.details["predicted"] == "electronics"


@pytest.mark.asyncio
async def test_integer_class_label_stringified() -> None:
    case = _case(Expected(outcome="3"))
    res = await ClassificationGrader().grade(_ctx(case, {"label": 3}))
    assert res.label == GradeLabel.PASS


def test_validate_path_rejects_malformed_extract() -> None:
    with pytest.raises(ValueError):
        ClassificationGrader(extract="bad..path")
    with pytest.raises(ValueError):
        ClassificationGrader(expected_from="also[bad")


# ---- expected_from path -----------------------------------------------------


@pytest.mark.asyncio
async def test_expected_from_reads_structured_output() -> None:
    case = _case(Expected(structured_output={"gold": "refund"}))
    grader = ClassificationGrader(expected_from="gold")
    res = await grader.grade(_ctx(case, {"label": "refund"}))
    assert res.label == GradeLabel.PASS
    assert res.details["expected"] == "refund"


# ---- degenerate inputs ------------------------------------------------------


@pytest.mark.asyncio
async def test_no_prediction_fails_with_mode() -> None:
    case = _case(Expected(outcome="full_order"))
    # structured_output has no "label" key → no usable prediction.
    res = await ClassificationGrader().grade(_ctx(case, {"other": "x"}))
    assert res.label == GradeLabel.FAIL
    assert res.failure_modes == [FM_NO_PREDICTION]
    assert res.breakdown is None


@pytest.mark.asyncio
async def test_list_prediction_is_not_a_class() -> None:
    case = _case(Expected(outcome="full_order"))
    res = await ClassificationGrader().grade(_ctx(case, {"label": ["a", "b"]}))
    assert res.label == GradeLabel.FAIL
    assert res.failure_modes == [FM_NO_PREDICTION]


@pytest.mark.asyncio
async def test_no_expected_class_fails_with_mode() -> None:
    case = _case(Expected())  # no outcome declared
    res = await ClassificationGrader().grade(_ctx(case, {"label": "full_order"}))
    assert res.label == GradeLabel.FAIL
    assert res.failure_modes == [FM_NO_EXPECTED]


@pytest.mark.asyncio
async def test_no_response_fails() -> None:
    case = _case(Expected(outcome="full_order"))
    res = await ClassificationGrader().grade(_ctx(case, None))
    assert res.label == GradeLabel.FAIL
    assert res.failure_modes == [FM_NO_PREDICTION]
