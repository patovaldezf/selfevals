from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from selfevals.graders.artifact import ArtifactCompletenessGrader
from selfevals.graders.base import GradeLabel, GraderContext
from selfevals.graders.llm_judge import LLMJudgeGrader, RubricTemplate
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


def _case(expected: Expected) -> EvalCase:
    return EvalCase(
        id=EvalCase.make_id(),
        workspace_id=WS,
        name="t",
        task_type="x",
        input={"messages": [{"role": "user", "content": "produce a report"}]},
        taxonomy=CaseTaxonomy(
            level=Level.FINAL_RESPONSE,
            feature=FeatureTag(primary="research.brief"),
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
            framework_version="selfevals/0.0.5",
            runtime="python-3.12",
            sandbox=SandboxMode.MOCK,
            started_at=T0,
        ),
        final_state=FinalState(status=TraceState.COMPLETED),
        spans=[AgentTurnSpan(id="sp_turn", name="t", started_at=T0)],
    )


def _ctx(case: EvalCase, artifact: dict[str, object] | None) -> GraderContext:
    response = AdapterResponse(content=None, structured_output=artifact)
    return GraderContext(case=case, trace=_trace(), response=response)


def _section_child(res_breakdown, section: str):  # type: ignore[no-untyped-def]
    assert res_breakdown is not None
    for child in res_breakdown.children:
        if child.key == f"section:{section}":
            return child
    raise AssertionError(f"no breakdown child for section {section!r}")


@pytest.mark.asyncio
async def test_all_sections_present_and_schema_valid_is_pass() -> None:
    case = _case(
        Expected(
            required_sections=["summary", "findings"],
            output_schema={
                "type": "object",
                "required": ["summary", "findings"],
                "properties": {"summary": {"type": "string"}, "findings": {"type": "array"}},
            },
        )
    )
    artifact = {"summary": "ok", "findings": ["a", "b"]}
    res = await ArtifactCompletenessGrader().grade(_ctx(case, artifact))
    assert res.label == GradeLabel.PASS
    assert res.score == 1.0
    assert res.breakdown is not None
    assert res.breakdown.key == "artifact_completeness"
    assert _section_child(res.breakdown, "summary").score == 1.0
    assert _section_child(res.breakdown, "findings").score == 1.0


@pytest.mark.asyncio
async def test_missing_section_is_partial_with_zero_child() -> None:
    case = _case(Expected(required_sections=["summary", "findings"]))
    artifact = {"summary": "ok"}  # `findings` absent
    res = await ArtifactCompletenessGrader().grade(_ctx(case, artifact))
    assert res.label == GradeLabel.PARTIAL
    assert res.score == pytest.approx(0.5)
    assert "missing_section" in res.failure_modes
    assert _section_child(res.breakdown, "summary").score == 1.0
    missing = _section_child(res.breakdown, "findings")
    assert missing.score == 0.0
    assert missing.label == GradeLabel.FAIL
    assert res.details["missing_sections"] == ["findings"]


@pytest.mark.asyncio
async def test_empty_string_section_counts_as_missing() -> None:
    case = _case(Expected(required_sections=["summary"]))
    res = await ArtifactCompletenessGrader().grade(_ctx(case, {"summary": ""}))
    assert res.label == GradeLabel.PARTIAL
    assert _section_child(res.breakdown, "summary").score == 0.0


@pytest.mark.asyncio
async def test_invalid_schema_is_fail() -> None:
    case = _case(
        Expected(
            output_schema={
                "type": "object",
                "required": ["summary"],
                "properties": {"summary": {"type": "string"}},
            }
        )
    )
    # `summary` is the wrong type -> schema invalid -> FAIL (not PARTIAL).
    res = await ArtifactCompletenessGrader().grade(_ctx(case, {"summary": 123}))
    assert res.label == GradeLabel.FAIL
    assert res.score == 0.0
    assert "schema_invalid" in res.failure_modes


@pytest.mark.asyncio
async def test_missing_required_property_is_fail() -> None:
    case = _case(
        Expected(
            output_schema={
                "type": "object",
                "required": ["summary", "findings"],
            }
        )
    )
    res = await ArtifactCompletenessGrader().grade(_ctx(case, {"summary": "ok"}))
    assert res.label == GradeLabel.FAIL
    assert "schema_invalid" in res.failure_modes


@pytest.mark.asyncio
async def test_nested_schema_validation() -> None:
    case = _case(
        Expected(
            output_schema={
                "type": "object",
                "properties": {
                    "meta": {
                        "type": "object",
                        "required": ["author"],
                        "properties": {"author": {"type": "string"}},
                    }
                },
            }
        )
    )
    good = await ArtifactCompletenessGrader().grade(_ctx(case, {"meta": {"author": "pat"}}))
    assert good.label == GradeLabel.PASS
    bad = await ArtifactCompletenessGrader().grade(_ctx(case, {"meta": {"author": 5}}))
    assert bad.label == GradeLabel.FAIL


@pytest.mark.asyncio
async def test_empty_structured_output_is_fail() -> None:
    case = _case(Expected(required_sections=["summary"]))
    res = await ArtifactCompletenessGrader().grade(_ctx(case, {}))
    assert res.label == GradeLabel.FAIL
    assert "empty_artifact" in res.failure_modes


@pytest.mark.asyncio
async def test_missing_structured_output_is_fail() -> None:
    case = _case(Expected(required_sections=["summary"]))
    res = await ArtifactCompletenessGrader().grade(_ctx(case, None))
    assert res.label == GradeLabel.FAIL
    assert "empty_artifact" in res.failure_modes


@pytest.mark.asyncio
async def test_no_response_at_all_is_fail() -> None:
    case = _case(Expected(required_sections=["summary"]))
    ctx = GraderContext(case=case, trace=_trace(), response=None)
    res = await ArtifactCompletenessGrader().grade(ctx)
    assert res.label == GradeLabel.FAIL
    assert "empty_artifact" in res.failure_modes


@pytest.mark.asyncio
async def test_one_child_per_required_section() -> None:
    case = _case(Expected(required_sections=["a", "b", "c"]))
    res = await ArtifactCompletenessGrader().grade(_ctx(case, {"a": "x", "b": "y", "c": "z"}))
    assert res.label == GradeLabel.PASS
    section_children = [c for c in res.breakdown.children if c.key.startswith("section:")]
    assert len(section_children) == 3
    assert {c.key for c in section_children} == {"section:a", "section:b", "section:c"}


@pytest.mark.asyncio
async def test_no_sections_validates_schema_only() -> None:
    # No required_sections: a non-empty artifact that satisfies the schema PASSes.
    case = _case(
        Expected(output_schema={"type": "object", "properties": {"x": {"type": "string"}}})
    )
    res = await ArtifactCompletenessGrader().grade(_ctx(case, {"x": "hello"}))
    assert res.label == GradeLabel.PASS
    assert res.score == 1.0
    section_children = [c for c in res.breakdown.children if c.key.startswith("section:")]
    assert section_children == []


@pytest.mark.asyncio
async def test_no_sections_no_schema_nonempty_is_pass() -> None:
    case = _case(Expected())
    res = await ArtifactCompletenessGrader().grade(_ctx(case, {"anything": "here"}))
    assert res.label == GradeLabel.PASS


@pytest.mark.asyncio
async def test_quality_judge_is_advisory_and_never_flips_verdict() -> None:
    # A judge that always says FAIL must NOT downgrade a deterministic PASS.
    def fn(_: AdapterRequest) -> AdapterResponse:
        return AdapterResponse(
            content=json.dumps({"label": "fail", "reason": "nope", "score": 0.0})
        )

    judge = LLMJudgeGrader(
        "quality",
        judge_adapter=EmbeddedAdapter(fn),
        rubric=RubricTemplate(rubric="is it good?"),
    )
    case = _case(Expected(required_sections=["summary"]))
    res = await ArtifactCompletenessGrader(quality_judge=judge).grade(
        _ctx(case, {"summary": "complete"})
    )
    assert res.label == GradeLabel.PASS
    assert res.score == 1.0
    # The advisory judge rides along as a weight-0 child and a details entry.
    quality_children = [c for c in res.breakdown.children if c.key.startswith("quality:")]
    assert len(quality_children) == 1
    assert quality_children[0].weight == 0.0
    assert quality_children[0].label == GradeLabel.FAIL
    assert res.details["quality"]["advisory"] is True
    assert res.details["quality"]["label"] == "fail"


@pytest.mark.asyncio
async def test_quality_judge_attached_even_on_fail() -> None:
    def fn(_: AdapterRequest) -> AdapterResponse:
        return AdapterResponse(content=json.dumps({"label": "pass", "reason": "great"}))

    judge = LLMJudgeGrader(
        "quality",
        judge_adapter=EmbeddedAdapter(fn),
        rubric=RubricTemplate(rubric="is it good?"),
    )
    case = _case(Expected(required_sections=["summary"]))
    # Empty artifact -> deterministic FAIL; judge says PASS but must not flip it.
    res = await ArtifactCompletenessGrader(quality_judge=judge).grade(_ctx(case, {}))
    assert res.label == GradeLabel.FAIL
    quality_children = [c for c in res.breakdown.children if c.key.startswith("quality:")]
    assert len(quality_children) == 1
    assert res.details["quality"]["label"] == "pass"


def test_empty_grader_name_rejected() -> None:
    with pytest.raises(ValueError):
        ArtifactCompletenessGrader(name="")
