from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from selfevals.graders.base import GradeLabel, GraderContext
from selfevals.graders.guardrail import (
    FM_DOUBLE_VALUE,
    FM_FORBIDDEN_PATTERN,
    FM_MISSING_REQUIRED_PATTERN,
    FM_PII_CREDIT_CARD,
    FM_PII_EMAIL,
    FM_PII_PHONE,
    FM_PII_SSN,
    FM_RUNTIME_GUARDRAIL,
    GuardrailGrader,
)
from selfevals.graders.registry import available_graders, resolve_graders
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
    GuardrailCheckSpan,
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
            ground_truth=GroundTruthSpec(methods=[GroundTruthMethod.EXACT_MATCH]),
            dataset_type=DatasetType.CAPABILITY,
        ),
        expected=Expected(),
    )


def _trace(*, guardrail_spans: list[tuple[str, bool]] | None = None) -> Trace:
    spans: list[Any] = [AgentTurnSpan(id="sp_turn", name="t", started_at=T0)]
    for i, (guardrail, passed) in enumerate(guardrail_spans or []):
        spans.append(
            GuardrailCheckSpan(
                id=f"sp_g_{i}",
                parent_id="sp_turn",
                name=guardrail,
                started_at=T0,
                guardrail=guardrail,
                passed=passed,
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


def _ctx(
    content: str | None, *, guardrail_spans: list[tuple[str, bool]] | None = None
) -> GraderContext:
    response = AdapterResponse(content=content) if content is not None else None
    return GraderContext(
        case=_case(), trace=_trace(guardrail_spans=guardrail_spans), response=response
    )


# --- clean baseline ---


@pytest.mark.asyncio
async def test_clean_response_passes() -> None:
    grader = GuardrailGrader(detect_pii=True, detect_double_value=True)
    res = await grader.grade(_ctx("Your order shipped. The total is $40.00."))
    assert res.label == GradeLabel.PASS
    assert res.score == 1.0
    assert res.failure_modes == []


@pytest.mark.asyncio
async def test_no_rules_no_spans_passes_trivially() -> None:
    res = await GuardrailGrader().grade(_ctx("anything at all, even an@email.com here"))
    assert res.label == GradeLabel.PASS


# --- forbidden / required regex ---


@pytest.mark.asyncio
async def test_forbidden_pattern_fails_blocking() -> None:
    grader = GuardrailGrader(forbidden_patterns=[r"(?i)\bpassword\b"])
    res = await grader.grade(_ctx("the admin Password is hunter2"))
    assert res.label == GradeLabel.FAIL  # blocking, never PARTIAL
    assert FM_FORBIDDEN_PATTERN in res.failure_modes
    assert res.score == 0.0


@pytest.mark.asyncio
async def test_required_pattern_missing_fails() -> None:
    grader = GuardrailGrader(required_patterns=[r"(?i)not financial advice"])
    res = await grader.grade(_ctx("buy this stock now"))
    assert res.label == GradeLabel.FAIL
    assert FM_MISSING_REQUIRED_PATTERN in res.failure_modes

    ok = await grader.grade(_ctx("buy this stock. This is not financial advice."))
    assert ok.label == GradeLabel.PASS


# --- PII detection ---


@pytest.mark.asyncio
async def test_pii_email_fails() -> None:
    res = await GuardrailGrader(detect_pii=True).grade(_ctx("reach me at jane.doe@example.com"))
    assert res.label == GradeLabel.FAIL
    assert FM_PII_EMAIL in res.failure_modes


@pytest.mark.asyncio
async def test_pii_phone_fails() -> None:
    res = await GuardrailGrader(detect_pii=True).grade(_ctx("call +1 415-555-0132 anytime"))
    assert res.label == GradeLabel.FAIL
    assert FM_PII_PHONE in res.failure_modes


@pytest.mark.asyncio
async def test_pii_ssn_fails() -> None:
    res = await GuardrailGrader(detect_pii=True).grade(_ctx("ssn 123-45-6789 on file"))
    assert res.label == GradeLabel.FAIL
    assert FM_PII_SSN in res.failure_modes


@pytest.mark.asyncio
async def test_pii_credit_card_luhn_valid_fails() -> None:
    # 4111 1111 1111 1111 is a Luhn-valid test card number.
    res = await GuardrailGrader(detect_pii=True).grade(_ctx("card 4111 1111 1111 1111"))
    assert res.label == GradeLabel.FAIL
    assert FM_PII_CREDIT_CARD in res.failure_modes


@pytest.mark.asyncio
async def test_pii_credit_card_luhn_invalid_ignored() -> None:
    # A 16-digit run that fails Luhn must not be flagged as a card.
    res = await GuardrailGrader(detect_pii=True, pii_categories=["credit_card"]).grade(
        _ctx("order ref 1234 5678 9012 3456")
    )
    assert res.label == GradeLabel.PASS


@pytest.mark.asyncio
async def test_pii_categories_can_be_narrowed() -> None:
    # Only scan for phone; an email present should not trip it.
    res = await GuardrailGrader(detect_pii=True, pii_categories=["phone"]).grade(
        _ctx("email a@b.com")
    )
    assert res.label == GradeLabel.PASS


def test_unknown_pii_category_rejected() -> None:
    with pytest.raises(ValueError):
        GuardrailGrader(detect_pii=True, pii_categories=["nope"])


# --- double-value heuristic ---


@pytest.mark.asyncio
async def test_double_value_contradiction_fails() -> None:
    grader = GuardrailGrader(detect_double_value=True)
    res = await grader.grade(_ctx("The total is $40.00 but your card was charged $4.00."))
    assert res.label == GradeLabel.FAIL
    assert FM_DOUBLE_VALUE in res.failure_modes


@pytest.mark.asyncio
async def test_repeated_identical_value_passes() -> None:
    grader = GuardrailGrader(detect_double_value=True)
    res = await grader.grade(_ctx("The total is $40.00. To confirm, that is $40.00."))
    assert res.label == GradeLabel.PASS


# --- GuardrailCheckSpan readout ---


@pytest.mark.asyncio
async def test_failed_guardrail_span_fails() -> None:
    res = await GuardrailGrader().grade(_ctx("clean text", guardrail_spans=[("moderation", False)]))
    assert res.label == GradeLabel.FAIL
    assert FM_RUNTIME_GUARDRAIL in res.failure_modes
    assert "moderation" in res.details["guardrail_spans_failed"]


@pytest.mark.asyncio
async def test_passed_guardrail_span_does_not_fail() -> None:
    res = await GuardrailGrader().grade(_ctx("clean text", guardrail_spans=[("moderation", True)]))
    assert res.label == GradeLabel.PASS
    assert res.details["guardrail_spans_failed"] == []


@pytest.mark.asyncio
async def test_guardrail_span_readout_can_be_disabled() -> None:
    res = await GuardrailGrader(read_guardrail_spans=False).grade(
        _ctx("clean text", guardrail_spans=[("moderation", False)])
    )
    assert res.label == GradeLabel.PASS


# --- failure-mode stability + aggregation ---


@pytest.mark.asyncio
async def test_multiple_violations_modes_stable_and_sorted() -> None:
    grader = GuardrailGrader(
        forbidden_patterns=[r"(?i)password"],
        detect_pii=True,
        pii_categories=["email"],
    )
    res = await grader.grade(_ctx("password leak: a@b.com", guardrail_spans=[("policy", False)]))
    assert res.label == GradeLabel.FAIL
    assert res.failure_modes == sorted(res.failure_modes)
    assert set(res.failure_modes) == {
        FM_FORBIDDEN_PATTERN,
        FM_PII_EMAIL,
        FM_RUNTIME_GUARDRAIL,
    }


@pytest.mark.asyncio
async def test_missing_response_treated_as_empty_text() -> None:
    grader = GuardrailGrader(required_patterns=[r"disclaimer"])
    res = await grader.grade(_ctx(None))
    assert res.label == GradeLabel.FAIL
    assert FM_MISSING_REQUIRED_PATTERN in res.failure_modes


# --- registry / instantiation ---


def test_empty_grader_name_rejected() -> None:
    with pytest.raises(ValueError):
        GuardrailGrader(name="")


def test_registered_and_resolvable() -> None:
    assert "guardrail" in available_graders()
    (grader,) = resolve_graders(["guardrail"])
    assert isinstance(grader, GuardrailGrader)
    assert grader.name == "guardrail"
