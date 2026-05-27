from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from selfevals.graders.base import GradeLabel, GraderContext
from selfevals.graders.llm_judge import LLMJudgeGrader, RubricTemplate
from selfevals.runner.adapters import AdapterRequest, AdapterResponse, EmbeddedAdapter
from selfevals.schemas.enums import (
    DatasetSource,
    DatasetType,
    GraderCardState,
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
from selfevals.schemas.grader_card import (
    CalibrationMetrics,
    CalibrationThresholds,
    GraderCard,
    GraderIO,
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


def _case() -> EvalCase:
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


def _ctx(content: str = "the answer") -> GraderContext:
    return GraderContext(
        case=_case(),
        trace=_trace(),
        response=AdapterResponse(content=content),
    )


def _judge_returning(payload: dict[str, object]) -> EmbeddedAdapter:
    def fn(_: AdapterRequest) -> AdapterResponse:
        return AdapterResponse(content=json.dumps(payload))

    return EmbeddedAdapter(fn)


_RUBRIC = RubricTemplate(rubric="Did the agent answer correctly?")


def test_happy_path_parses_judge_response() -> None:
    judge = _judge_returning(
        {"label": "pass", "reason": "matches", "score": 0.9, "confidence": 0.8}
    )
    grader = LLMJudgeGrader("rubric_v1", judge_adapter=judge, rubric=_RUBRIC)
    res = grader.grade(_ctx())
    assert res.label == GradeLabel.PASS
    assert res.score == 0.9
    assert res.confidence == 0.8
    assert "matches" in res.reason


def test_unknown_label_marked_error() -> None:
    judge = _judge_returning({"label": "great", "reason": "x"})
    grader = LLMJudgeGrader("g", judge_adapter=judge, rubric=_RUBRIC)
    res = grader.grade(_ctx())
    assert res.label == GradeLabel.ERROR
    assert "unknown label" in res.reason


def test_invalid_json_marked_error() -> None:
    def fn(_: AdapterRequest) -> AdapterResponse:
        return AdapterResponse(content="not json at all")

    grader = LLMJudgeGrader("g", judge_adapter=EmbeddedAdapter(fn), rubric=_RUBRIC)
    res = grader.grade(_ctx())
    assert res.label == GradeLabel.ERROR


def test_judge_exception_marked_error() -> None:
    def fn(_: AdapterRequest) -> AdapterResponse:
        raise RuntimeError("upstream rate-limited")

    grader = LLMJudgeGrader("g", judge_adapter=EmbeddedAdapter(fn), rubric=_RUBRIC)
    res = grader.grade(_ctx())
    assert res.label == GradeLabel.ERROR
    assert "upstream rate-limited" in res.reason


def test_blocking_card_below_threshold_skips_unless_forced() -> None:
    card = GraderCard(
        id=GraderCard.make_id(),
        workspace_id=WS,
        name="rubric",
        purpose="x",
        grader_kind="llm_judge",
        method=GroundTruthMethod.LLM_JUDGE,
        blocking=True,
        io=GraderIO(input_fields=["agent_response"]),
        thresholds=CalibrationThresholds(
            min_precision=0.90,
            min_recall=0.95,
            max_high_risk_false_negatives=0,
        ),
        # Below thresholds.
        metrics=CalibrationMetrics(precision=0.7, recall=0.99, high_risk_false_negatives=0),
        state=GraderCardState.DRIFTING,
    )
    judge = _judge_returning({"label": "pass", "reason": "ok"})
    grader = LLMJudgeGrader("g", judge_adapter=judge, rubric=_RUBRIC, card=card)
    res = grader.grade(_ctx())
    assert res.label == GradeLabel.SKIPPED
    assert "degraded to advisory" in res.reason

    # force=True overrides.
    grader2 = LLMJudgeGrader("g", judge_adapter=judge, rubric=_RUBRIC, card=card, force=True)
    assert grader2.grade(_ctx()).label == GradeLabel.PASS


def test_blocking_card_meeting_thresholds_runs_normally() -> None:
    card = GraderCard(
        id=GraderCard.make_id(),
        workspace_id=WS,
        name="rubric",
        purpose="x",
        grader_kind="llm_judge",
        method=GroundTruthMethod.LLM_JUDGE,
        blocking=True,
        io=GraderIO(input_fields=["agent_response"]),
        thresholds=CalibrationThresholds(
            min_precision=0.90,
            min_recall=0.95,
            max_high_risk_false_negatives=0,
        ),
        metrics=CalibrationMetrics(precision=0.95, recall=0.97, high_risk_false_negatives=0),
    )
    judge = _judge_returning({"label": "pass", "reason": "ok"})
    grader = LLMJudgeGrader("g", judge_adapter=judge, rubric=_RUBRIC, card=card)
    res = grader.grade(_ctx())
    assert res.label == GradeLabel.PASS


def test_empty_name_rejected() -> None:
    with pytest.raises(ValueError):
        LLMJudgeGrader(
            "", judge_adapter=_judge_returning({"label": "pass", "reason": "x"}), rubric=_RUBRIC
        )
