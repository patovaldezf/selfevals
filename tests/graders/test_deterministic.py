from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from selfevals.graders.base import GradeLabel, GraderContext
from selfevals.graders.deterministic import DeterministicGrader
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
    EnvironmentInfo,
    FinalState,
    LLMCallSpan,
    LLMOutput,
    RunInfo,
    ToolCallSpan,
    ToolUseRequest,
    Trace,
)

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"
T0 = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)


def _case(expected: Expected) -> EvalCase:
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
        expected=expected,
    )


def _trace(*, tool_uses: list[tuple[str, str]] | None = None) -> Trace:
    tool_uses = tool_uses or []
    spans: list[Any] = [AgentTurnSpan(id="sp_turn", name="t", started_at=T0)]
    if tool_uses:
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
        for name, tid in tool_uses:
            spans.append(
                ToolCallSpan(
                    id=f"sp_t_{tid}",
                    parent_id="sp_llm",
                    name=name,
                    started_at=T0,
                    tool_name=name,
                    tool_use_id=tid,
                    status=ToolCallStatus.OK,
                )
            )
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


@pytest.mark.asyncio
async def test_pass_when_all_rules_satisfied() -> None:
    case = _case(Expected(must_include=["pong"]))
    res = await DeterministicGrader().grade(_ctx(case, _trace(), content="pong reply"))
    assert res.label == GradeLabel.PASS
    assert res.score == 1.0


@pytest.mark.asyncio
async def test_must_include_case_insensitive_by_default() -> None:
    case = _case(Expected(must_include=["Pong"]))
    res = await DeterministicGrader().grade(_ctx(case, _trace(), content="pong"))
    assert res.label == GradeLabel.PASS


@pytest.mark.asyncio
async def test_case_sensitive_mode() -> None:
    case = _case(Expected(must_include=["Pong"]))
    res = await DeterministicGrader(case_sensitive=True).grade(_ctx(case, _trace(), content="pong"))
    assert res.label == GradeLabel.FAIL
    assert "missing_required_substring" in res.failure_modes


@pytest.mark.asyncio
async def test_must_not_include_violation() -> None:
    case = _case(Expected(must_not_include=["secret"]))
    res = await DeterministicGrader().grade(_ctx(case, _trace(), content="here is a secret"))
    assert res.label == GradeLabel.FAIL
    assert "forbidden_substring" in res.failure_modes


@pytest.mark.asyncio
async def test_required_tool_must_appear_in_trace() -> None:
    case = _case(Expected(required_tools=["search"]))
    # No tool calls in trace.
    res = await DeterministicGrader().grade(_ctx(case, _trace(), content="ok"))
    assert res.label == GradeLabel.FAIL
    assert "missing_required_tool" in res.failure_modes
    # Now with the tool present.
    res2 = await DeterministicGrader().grade(
        _ctx(case, _trace(tool_uses=[("search", "toolu_01")]), content="ok")
    )
    assert res2.label == GradeLabel.PASS


@pytest.mark.asyncio
async def test_forbidden_tool_invoked() -> None:
    case = _case(Expected(forbidden_tools=["delete"]))
    res = await DeterministicGrader().grade(
        _ctx(case, _trace(tool_uses=[("delete", "toolu_01")]), content="ok")
    )
    assert res.label == GradeLabel.FAIL
    assert "forbidden_tool_invoked" in res.failure_modes


@pytest.mark.asyncio
async def test_regex_match() -> None:
    case = _case(Expected())
    grader = DeterministicGrader(regex_match=r"^ORD-\d{4}$")
    res = await grader.grade(_ctx(case, _trace(), content="ORD-1234"))
    assert res.label == GradeLabel.PASS
    res2 = await grader.grade(_ctx(case, _trace(), content="not matching"))
    assert res2.label == GradeLabel.FAIL
    assert "regex_mismatch" in res2.failure_modes


@pytest.mark.asyncio
async def test_structured_output_equality() -> None:
    case = _case(Expected(structured_output={"sku": "ABC-1", "qty": 2}))
    response = AdapterResponse(content=None, structured_output={"sku": "ABC-1", "qty": 2})
    ctx = GraderContext(case=case, trace=_trace(), response=response)
    assert (await DeterministicGrader().grade(ctx)).label == GradeLabel.PASS
    bad_response = AdapterResponse(content=None, structured_output={"sku": "ABC-1", "qty": 1})
    bad_ctx = GraderContext(case=case, trace=_trace(), response=bad_response)
    res = await DeterministicGrader().grade(bad_ctx)
    assert res.label == GradeLabel.FAIL
    assert "structured_output_mismatch" in res.failure_modes


@pytest.mark.asyncio
async def test_multiple_violations_reported() -> None:
    case = _case(
        Expected(must_include=["xenon"], must_not_include=["bug"], required_tools=["search"])
    )
    res = await DeterministicGrader().grade(_ctx(case, _trace(), content="contains bug only"))
    assert res.label == GradeLabel.FAIL
    assert set(res.failure_modes) >= {
        "missing_required_substring",
        "forbidden_substring",
        "missing_required_tool",
    }


@pytest.mark.asyncio
async def test_missing_response_treated_as_empty_text() -> None:
    case = _case(Expected(must_include=["x"]))
    res = await DeterministicGrader().grade(GraderContext(case=case, trace=_trace(), response=None))
    assert res.label == GradeLabel.FAIL


@pytest.mark.asyncio
async def test_min_recall_below_threshold_fails_with_fractional_score() -> None:
    # 1 of 3 required substrings present -> recall 0.333 < 0.8 -> FAIL.
    case = _case(Expected(must_include=["alpha", "beta", "gamma"], min_recall=0.8))
    res = await DeterministicGrader().grade(_ctx(case, _trace(), content="alpha only"))
    assert res.label == GradeLabel.FAIL
    assert res.score == pytest.approx(1 / 3)
    assert "missing_required_substring" in res.failure_modes
    assert res.details["recall"] == pytest.approx(1 / 3)


@pytest.mark.asyncio
async def test_min_recall_met_passes_with_fractional_score() -> None:
    # 2 of 3 required substrings present -> recall 0.667 >= 0.5 -> PASS.
    case = _case(Expected(must_include=["alpha", "beta", "gamma"], min_recall=0.5))
    res = await DeterministicGrader().grade(_ctx(case, _trace(), content="alpha and beta"))
    assert res.label == GradeLabel.PASS
    assert res.score == pytest.approx(2 / 3)
    # Diagnostics for the missing item survive even on PASS.
    assert "missing_required_substring" in res.failure_modes


@pytest.mark.asyncio
async def test_min_recall_none_preserves_all_or_nothing() -> None:
    # Regression guard: without min_recall, any missing substring is FAIL/0.0.
    case = _case(Expected(must_include=["alpha", "beta", "gamma"]))
    res = await DeterministicGrader().grade(_ctx(case, _trace(), content="alpha and beta"))
    assert res.label == GradeLabel.FAIL
    assert res.score == 0.0
    assert "missing_required_substring" in res.failure_modes


@pytest.mark.asyncio
async def test_min_recall_met_but_must_not_include_violated_still_fails() -> None:
    # Recall passes (both present) but a hard violation takes precedence -> FAIL.
    case = _case(
        Expected(
            must_include=["alpha", "beta"],
            min_recall=0.5,
            must_not_include=["secret"],
        )
    )
    res = await DeterministicGrader().grade(_ctx(case, _trace(), content="alpha beta and a secret"))
    assert res.label == GradeLabel.FAIL
    # Score still reflects the recall dimension.
    assert res.score == pytest.approx(1.0)
    assert "forbidden_substring" in res.failure_modes


@pytest.mark.asyncio
async def test_min_recall_set_with_empty_must_include_does_not_crash() -> None:
    # No required substrings: recall is vacuously satisfied -> PASS with score 1.0.
    case = _case(Expected(must_include=[], min_recall=0.8))
    res = await DeterministicGrader().grade(_ctx(case, _trace(), content="anything"))
    assert res.label == GradeLabel.PASS
    assert res.score == 1.0


def test_empty_grader_name_rejected() -> None:
    with pytest.raises(ValueError):
        DeterministicGrader(name="")
