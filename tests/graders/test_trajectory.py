from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from selfevals.graders.base import GradeLabel, Grader, GraderContext, GradeResult
from selfevals.graders.trajectory import (
    FM_HARD_FORBIDDEN_TOOL,
    FM_HARD_MAX_TOOL_CALLS,
    FM_MISSING_ROUTING_DECISION,
    FM_REDUNDANT_RETRIEVAL,
    FM_TOOL_LOOP_OVERRUN,
    FM_WRONG_TOOL_ORDER,
    HardInvariants,
    TrajectoryGrader,
)
from selfevals.runner.adapters import AdapterResponse
from selfevals.schemas.enums import (
    DatasetSource,
    DatasetType,
    GroundTruthMethod,
    Level,
    SandboxMode,
    StopReason,
    ToolCallStatus,
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
    DecisionSpan,
    EnvironmentInfo,
    FinalState,
    HandoffSpan,
    LLMCallSpan,
    LLMOutput,
    RetrievalSpan,
    RunInfo,
    ToolCallSpan,
    ToolUseRequest,
    Trace,
)

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"
T0 = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)


def _case(expected: Expected | None = None) -> EvalCase:
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
        expected=expected or Expected(),
    )


def _trace(extra_spans: list[Any] | None = None, *, tools: list[str] | None = None) -> Trace:
    """Build a trace. `tools` adds ToolCallSpans (with a linking LLMCallSpan)
    in order; `extra_spans` appends arbitrary already-built spans."""
    spans: list[Any] = [AgentTurnSpan(id="sp_turn", name="t", started_at=T0)]
    tools = tools or []
    if tools:
        tool_uses = [(name, f"toolu_{i:02d}") for i, name in enumerate(tools)]
        spans.append(
            LLMCallSpan(
                id="sp_llm",
                parent_id="sp_turn",
                name="m",
                started_at=T0,
                provider="anthropic",
                model="claude-sonnet-4-6",
                output=LLMOutput(
                    stop_reason=StopReason.TOOL_USE,
                    tool_use_requested=[
                        ToolUseRequest(tool=name, tool_use_id=tid) for (name, tid) in tool_uses
                    ],
                ),
            )
        )
        for i, (name, tid) in enumerate(tool_uses):
            spans.append(
                ToolCallSpan(
                    id=f"sp_t_{i:02d}",
                    parent_id="sp_llm",
                    name=name,
                    started_at=T0,
                    tool_name=name,
                    tool_use_id=tid,
                    status=ToolCallStatus.OK,
                )
            )
    if extra_spans:
        spans.extend(extra_spans)
    return Trace(
        id=Trace.make_id(),
        workspace_id=WS,
        run=RunInfo(run_id="run_01"),
        agent=AgentSnapshotRef(agent_id="ag_x", agent_version=1),
        environment=EnvironmentInfo(
            framework_version="selfevals/0.0.5",
            runtime="python-3.12",
            sandbox=SandboxMode.MOCK,
            started_at=T0,
        ),
        final_state=FinalState(status=TraceState.COMPLETED),
        spans=spans,
    )


def _ctx(case: EvalCase, trace: Trace, *, content: str | None = "ok") -> GraderContext:
    response = AdapterResponse(content=content) if content is not None else None
    return GraderContext(case=case, trace=trace, response=response)


def _retrieval(
    span_id: str, *, query_hash: str | None, query_pointer: str | None = None
) -> RetrievalSpan:
    return RetrievalSpan(
        id=span_id,
        parent_id="sp_turn",
        name="r",
        started_at=T0,
        retriever="vector",
        query_hash=query_hash,
        query_pointer=query_pointer,
        top_k_requested=5,
    )


def _decision(span_id: str) -> DecisionSpan:
    return DecisionSpan(
        id=span_id,
        parent_id="sp_turn",
        name="d",
        started_at=T0,
        decision_type="route",
        chosen="agent_b",
    )


def _handoff(span_id: str) -> HandoffSpan:
    return HandoffSpan(
        id=span_id,
        parent_id="sp_turn",
        name="h",
        started_at=T0,
        target="agent_b",
    )


def _child(res: GradeResult, key: str) -> Any:
    assert res.breakdown is not None
    for c in res.breakdown.children:
        if c.key == key:
            return c
    return None


# --- core contract: output-state authoritative, trajectory advisory ---


@pytest.mark.asyncio
async def test_good_output_bad_trajectory_still_passes() -> None:
    # Output satisfies must_include => PASS. Trajectory is dreadful (wrong
    # order, loop, redundant retrieval, missing routing) but all advisory.
    case = _case(Expected(must_include=["pong"]))
    trace = _trace(
        tools=["search", "search", "search", "search"],
        extra_spans=[
            _handoff("sp_handoff"),
            _retrieval("sp_r1", query_hash="qh_1"),
            _retrieval("sp_r2", query_hash="qh_1"),
        ],
    )
    grader = TrajectoryGrader(expected_tool_order=["fetch", "search"])
    res = await grader.grade(_ctx(case, trace, content="pong reply"))

    # Verdict comes from output-state and is NOT flipped by trajectory.
    assert res.label == GradeLabel.PASS
    assert res.score == 1.0

    # All four diagnostic children present and advisory (weight == 0).
    assert res.breakdown is not None
    assert res.breakdown.key == "trajectory"
    keys = {c.key for c in res.breakdown.children}
    assert keys == {
        "wrong_tool_order",
        "tool_loop_overrun",
        "missing_routing_decision",
        "redundant_retrieval",
    }
    for c in res.breakdown.children:
        assert c.weight == 0.0
        assert c.label == GradeLabel.FAIL


@pytest.mark.asyncio
async def test_clean_trajectory_has_no_diagnostic_children() -> None:
    case = _case(Expected(must_include=["pong"]))
    trace = _trace(tools=["search"])
    res = await TrajectoryGrader().grade(_ctx(case, trace, content="pong"))
    assert res.label == GradeLabel.PASS
    assert res.breakdown is not None
    assert res.breakdown.children == []


@pytest.mark.asyncio
async def test_output_fail_is_authoritative_even_with_clean_trajectory() -> None:
    case = _case(Expected(must_include=["pong"]))
    trace = _trace(tools=["search"])
    res = await TrajectoryGrader().grade(_ctx(case, trace, content="no match here"))
    assert res.label == GradeLabel.FAIL
    assert "missing_required_substring" in res.failure_modes


# --- each diagnostic mode in isolation ---


@pytest.mark.asyncio
async def test_wrong_tool_order_diagnostic() -> None:
    case = _case()
    # invoked: [search, fetch]; expected order [fetch, search] is NOT a
    # subsequence => wrong order.
    trace = _trace(tools=["search", "fetch"])
    res = await TrajectoryGrader(expected_tool_order=["fetch", "search"]).grade(_ctx(case, trace))
    node = _child(res, "wrong_tool_order")
    assert node is not None
    assert node.weight == 0.0
    assert FM_WRONG_TOOL_ORDER in node.failure_modes
    # label unaffected
    assert res.label == GradeLabel.PASS


@pytest.mark.asyncio
async def test_correct_tool_order_subsequence_no_diagnostic() -> None:
    case = _case()
    # expected [fetch, search] IS an ordered subsequence of invoked.
    trace = _trace(tools=["fetch", "log", "search"])
    res = await TrajectoryGrader(expected_tool_order=["fetch", "search"]).grade(_ctx(case, trace))
    assert _child(res, "wrong_tool_order") is None


@pytest.mark.asyncio
async def test_tool_loop_overrun_consecutive() -> None:
    case = _case()
    trace = _trace(tools=["search", "search", "search", "search"])
    res = await TrajectoryGrader(max_consecutive_tool_calls=3).grade(_ctx(case, trace))
    node = _child(res, "tool_loop_overrun")
    assert node is not None
    assert node.weight == 0.0
    assert FM_TOOL_LOOP_OVERRUN in node.failure_modes


@pytest.mark.asyncio
async def test_tool_loop_overrun_total() -> None:
    case = _case()
    # Not consecutive (alternating) but 3 total > max_total=2.
    trace = _trace(tools=["search", "fetch", "search", "fetch", "search"])
    res = await TrajectoryGrader(max_consecutive_tool_calls=10, max_total_tool_calls=2).grade(
        _ctx(case, trace)
    )
    node = _child(res, "tool_loop_overrun")
    assert node is not None
    assert FM_TOOL_LOOP_OVERRUN in node.failure_modes


@pytest.mark.asyncio
async def test_missing_routing_decision_on_handoff() -> None:
    case = _case()
    trace = _trace(extra_spans=[_handoff("sp_handoff")])
    res = await TrajectoryGrader().grade(_ctx(case, trace))
    node = _child(res, "missing_routing_decision")
    assert node is not None
    assert FM_MISSING_ROUTING_DECISION in node.failure_modes


@pytest.mark.asyncio
async def test_missing_routing_decision_when_expected() -> None:
    case = _case()
    trace = _trace()  # no handoff, no decision
    res = await TrajectoryGrader(expect_routing_decision=True).grade(_ctx(case, trace))
    assert _child(res, "missing_routing_decision") is not None


@pytest.mark.asyncio
async def test_routing_decision_present_suppresses_diagnostic() -> None:
    case = _case()
    trace = _trace(extra_spans=[_handoff("sp_handoff"), _decision("sp_decision")])
    res = await TrajectoryGrader(expect_routing_decision=True).grade(_ctx(case, trace))
    assert _child(res, "missing_routing_decision") is None


@pytest.mark.asyncio
async def test_redundant_retrieval_by_query_hash() -> None:
    case = _case()
    trace = _trace(
        extra_spans=[
            _retrieval("sp_r1", query_hash="qh_a"),
            _retrieval("sp_r2", query_hash="qh_a"),
        ]
    )
    res = await TrajectoryGrader().grade(_ctx(case, trace))
    node = _child(res, "redundant_retrieval")
    assert node is not None
    assert FM_REDUNDANT_RETRIEVAL in node.failure_modes


@pytest.mark.asyncio
async def test_redundant_retrieval_fallback_query_pointer() -> None:
    case = _case()
    trace = _trace(
        extra_spans=[
            _retrieval("sp_r1", query_hash=None, query_pointer="ptr://q"),
            _retrieval("sp_r2", query_hash=None, query_pointer="ptr://q"),
        ]
    )
    res = await TrajectoryGrader().grade(_ctx(case, trace))
    assert _child(res, "redundant_retrieval") is not None


@pytest.mark.asyncio
async def test_distinct_retrievals_no_diagnostic() -> None:
    case = _case()
    trace = _trace(
        extra_spans=[
            _retrieval("sp_r1", query_hash="qh_a"),
            _retrieval("sp_r2", query_hash="qh_b"),
        ]
    )
    res = await TrajectoryGrader().grade(_ctx(case, trace))
    assert _child(res, "redundant_retrieval") is None


# --- hard invariants: the only trajectory signal that flips the verdict ---


@pytest.mark.asyncio
async def test_hard_forbidden_tool_flips_to_fail() -> None:
    # Output would PASS, but a forbidden tool was invoked => FAIL.
    case = _case(Expected(must_include=["pong"]))
    trace = _trace(tools=["search", "delete"])
    grader = TrajectoryGrader(hard_invariants=HardInvariants(forbidden_tools=frozenset({"delete"})))
    res = await grader.grade(_ctx(case, trace, content="pong"))
    assert res.label == GradeLabel.FAIL
    assert res.score == 0.0
    assert FM_HARD_FORBIDDEN_TOOL in res.failure_modes
    # Recorded as a real (weight 1.0) node, not advisory.
    node = _child(res, "hard_forbidden_tool:delete")
    assert node is not None
    assert node.weight == 1.0


@pytest.mark.asyncio
async def test_hard_max_tool_calls_flips_to_fail() -> None:
    case = _case(Expected(must_include=["pong"]))
    trace = _trace(tools=["a", "b", "c"])
    grader = TrajectoryGrader(hard_invariants=HardInvariants(max_tool_calls=2))
    res = await grader.grade(_ctx(case, trace, content="pong"))
    assert res.label == GradeLabel.FAIL
    assert FM_HARD_MAX_TOOL_CALLS in res.failure_modes
    node = _child(res, "hard_max_tool_calls")
    assert node is not None
    assert node.weight == 1.0


@pytest.mark.asyncio
async def test_hard_invariant_within_limit_does_not_flip() -> None:
    case = _case(Expected(must_include=["pong"]))
    trace = _trace(tools=["a", "b"])
    grader = TrajectoryGrader(
        hard_invariants=HardInvariants(forbidden_tools=frozenset({"delete"}), max_tool_calls=3)
    )
    res = await grader.grade(_ctx(case, trace, content="pong"))
    assert res.label == GradeLabel.PASS
    assert res.score == 1.0


# --- injectable output grader ---


class _AlwaysPartial(Grader):
    name = "always_partial"

    async def grade(self, context: GraderContext) -> GradeResult:
        return GradeResult(
            grader=self.name,
            label=GradeLabel.PARTIAL,
            reason="partial by fiat",
            score=0.5,
            confidence=0.9,
            failure_modes=["custom_mode"],
        )


@pytest.mark.asyncio
async def test_injectable_output_grader_is_authoritative() -> None:
    case = _case()
    trace = _trace(tools=["search"])
    res = await TrajectoryGrader(output_grader=_AlwaysPartial()).grade(_ctx(case, trace))
    assert res.label == GradeLabel.PARTIAL
    assert res.score == 0.5
    assert res.confidence == 0.9
    assert "custom_mode" in res.failure_modes
    assert res.details["output_grader"] == "always_partial"


@pytest.mark.asyncio
async def test_hard_invariant_flips_even_injected_partial() -> None:
    case = _case()
    trace = _trace(tools=["delete"])
    grader = TrajectoryGrader(
        output_grader=_AlwaysPartial(),
        hard_invariants=HardInvariants(forbidden_tools=frozenset({"delete"})),
    )
    res = await grader.grade(_ctx(case, trace))
    assert res.label == GradeLabel.FAIL
    assert "custom_mode" in res.failure_modes
    assert FM_HARD_FORBIDDEN_TOOL in res.failure_modes


# --- registry + construction ---


def test_registered_in_registry() -> None:
    from selfevals.graders.registry import available_graders, resolve_graders

    assert "trajectory" in available_graders()
    (grader,) = resolve_graders(["trajectory"])
    assert isinstance(grader, TrajectoryGrader)


def test_empty_name_rejected() -> None:
    with pytest.raises(ValueError):
        TrajectoryGrader(name="")


def test_invalid_max_consecutive_rejected() -> None:
    with pytest.raises(ValueError):
        TrajectoryGrader(max_consecutive_tool_calls=0)
