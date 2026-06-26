from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from selfevals.graders.base import GradeLabel, GraderContext
from selfevals.graders.pairwise import (
    FM_WORSE_THAN_REFERENCE,
    PairwiseGrader,
    PairwiseRubric,
)
from selfevals.runner.adapters import AdapterRequest, AdapterResponse, EmbeddedAdapter
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
_RUBRIC = PairwiseRubric(rubric="Which answer is more helpful?")


def _case(reference: str | None) -> EvalCase:
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
            ground_truth=GroundTruthSpec(methods=[GroundTruthMethod.LLM_JUDGE]),
            dataset_type=DatasetType.CAPABILITY,
        ),
        expected=Expected(),
        reference_output=reference,
    )


def _trace() -> Trace:
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
        spans=[AgentTurnSpan(id="sp_turn", name="t", started_at=T0)],
    )


def _ctx(reference: str | None = "gold answer", content: str = "agent answer") -> GraderContext:
    return GraderContext(
        case=_case(reference),
        trace=_trace(),
        response=AdapterResponse(content=content),
    )


def _judge_returning(payload: dict[str, object]) -> EmbeddedAdapter:
    def fn(_: AdapterRequest) -> AdapterResponse:
        return AdapterResponse(content=json.dumps(payload))

    return EmbeddedAdapter(fn)


@pytest.mark.asyncio
async def test_agent_wins_maps_to_pass() -> None:
    judge = _judge_returning({"preferred": "a", "margin": 0.8, "reason": "A clearer"})
    grader = PairwiseGrader("taste", judge_adapter=judge, rubric=_RUBRIC)
    res = await grader.grade(_ctx())
    assert res.label == GradeLabel.PASS
    assert res.score == pytest.approx(0.9)
    assert res.details["preferred"] == "a"


@pytest.mark.asyncio
async def test_reference_wins_maps_to_fail_with_failure_mode() -> None:
    judge = _judge_returning({"preferred": "b", "margin": 0.6, "reason": "B better"})
    grader = PairwiseGrader("taste", judge_adapter=judge, rubric=_RUBRIC)
    res = await grader.grade(_ctx())
    assert res.label == GradeLabel.FAIL
    assert res.score == pytest.approx(0.2)
    assert FM_WORSE_THAN_REFERENCE in res.failure_modes


@pytest.mark.asyncio
async def test_tie_is_pass_by_default() -> None:
    judge = _judge_returning({"preferred": "tie", "margin": 0.0, "reason": "even"})
    grader = PairwiseGrader("taste", judge_adapter=judge, rubric=_RUBRIC)
    res = await grader.grade(_ctx())
    assert res.label == GradeLabel.PASS
    assert res.score == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_tie_is_fail_when_configured() -> None:
    judge = _judge_returning({"preferred": "tie", "margin": 0.0, "reason": "even"})
    grader = PairwiseGrader("taste", judge_adapter=judge, rubric=_RUBRIC, tie_is_pass=False)
    res = await grader.grade(_ctx())
    assert res.label == GradeLabel.FAIL


@pytest.mark.asyncio
async def test_no_reference_skips() -> None:
    judge = _judge_returning({"preferred": "a", "margin": 0.5, "reason": "x"})
    grader = PairwiseGrader("taste", judge_adapter=judge, rubric=_RUBRIC)
    res = await grader.grade(_ctx(reference=None))
    assert res.label == GradeLabel.SKIPPED


@pytest.mark.asyncio
async def test_invalid_json_marked_error() -> None:
    def fn(_: AdapterRequest) -> AdapterResponse:
        return AdapterResponse(content="not json")

    grader = PairwiseGrader("taste", judge_adapter=EmbeddedAdapter(fn), rubric=_RUBRIC)
    res = await grader.grade(_ctx())
    assert res.label == GradeLabel.ERROR


@pytest.mark.asyncio
async def test_unknown_preferred_marked_error() -> None:
    judge = _judge_returning({"preferred": "neither", "margin": 0.5, "reason": "x"})
    grader = PairwiseGrader("taste", judge_adapter=judge, rubric=_RUBRIC)
    res = await grader.grade(_ctx())
    assert res.label == GradeLabel.ERROR


@pytest.mark.asyncio
async def test_judge_exception_marked_error() -> None:
    def fn(_: AdapterRequest) -> AdapterResponse:
        raise RuntimeError("rate limited")

    grader = PairwiseGrader("taste", judge_adapter=EmbeddedAdapter(fn), rubric=_RUBRIC)
    res = await grader.grade(_ctx())
    assert res.label == GradeLabel.ERROR
    assert "rate limited" in res.reason


@pytest.mark.asyncio
async def test_swap_and_average_cancels_position_bias() -> None:
    # A judge that always prefers whichever response is shown FIRST.
    # Unswapped: A is first -> prefers "a". Swapped: B is first (shown as A) and
    # the verdict is flipped back, so it favors "b". Averaged -> tie.
    def fn(req: AdapterRequest) -> AdapterResponse:
        return AdapterResponse(content=json.dumps({"preferred": "a", "margin": 0.8, "reason": "first"}))

    grader = PairwiseGrader(
        "taste", judge_adapter=EmbeddedAdapter(fn), rubric=_RUBRIC, swap_and_average=True
    )
    res = await grader.grade(_ctx())
    assert res.label == GradeLabel.PASS
    assert res.score == pytest.approx(0.5)
    assert res.details["preferred"] == "tie"


@pytest.mark.asyncio
async def test_swap_and_average_keeps_consistent_winner() -> None:
    # A judge that consistently prefers the REAL agent answer regardless of
    # position. The prompt renders "Response A:" then "Response B:", so whichever
    # the agent answer is shown as is decided by which label precedes it.
    def fn(req: AdapterRequest) -> AdapterResponse:
        prompt = req.input["messages"][0]["content"]
        agent_at = prompt.index("agent answer")
        gold_at = prompt.index("gold answer")
        # The agent answer sits in slot A when it appears before the gold answer.
        preferred = "a" if agent_at < gold_at else "b"
        return AdapterResponse(
            content=json.dumps({"preferred": preferred, "margin": 0.6, "reason": "agent better"})
        )

    grader = PairwiseGrader(
        "taste", judge_adapter=EmbeddedAdapter(fn), rubric=_RUBRIC, swap_and_average=True
    )
    res = await grader.grade(_ctx())
    assert res.label == GradeLabel.PASS
    assert res.details["preferred"] == "a"
    assert res.score == pytest.approx(0.8)
