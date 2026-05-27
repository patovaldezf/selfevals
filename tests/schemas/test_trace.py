from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from selfeval.schemas.enums import SandboxMode, StopReason, ToolCallStatus, TraceState
from selfeval.schemas.trace import (
    TRACE_SCHEMA_VERSION,
    AgentSnapshotRef,
    AgentTurnSpan,
    CostBreakdown,
    EnvironmentInfo,
    ErrorSpan,
    FinalState,
    GuardrailCheckSpan,
    LLMCallSpan,
    LLMOutput,
    ReasoningBlock,
    RetrievalSpan,
    RetrievedDoc,
    RunInfo,
    TokenBreakdown,
    ToolCallSpan,
    ToolUseRequest,
    Trace,
    TraceLink,
)

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"
T0 = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)


def _env(**overrides: Any) -> EnvironmentInfo:
    base: dict[str, Any] = {
        "framework_version": "selfeval/0.0.1",
        "runtime": "python-3.12",
        "sandbox": SandboxMode.MOCK,
        "started_at": T0,
    }
    base.update(overrides)
    return EnvironmentInfo(**base)


def _trace(**overrides: Any) -> Trace:
    base: dict[str, Any] = {
        "id": Trace.make_id(),
        "workspace_id": WS,
        "run": RunInfo(run_id="run_01"),
        "agent": AgentSnapshotRef(agent_id="ag_x", agent_version=1),
        "environment": _env(),
        "final_state": FinalState(status=TraceState.COMPLETED),
    }
    base.update(overrides)
    return Trace(**base)


def test_trace_minimal() -> None:
    t = _trace()
    assert t.schema_version == TRACE_SCHEMA_VERSION
    assert t.final_state.status == TraceState.COMPLETED
    assert t.spans == []


def test_environment_ended_after_started() -> None:
    with pytest.raises(ValidationError):
        EnvironmentInfo(
            framework_version="v",
            runtime="r",
            sandbox=SandboxMode.MOCK,
            started_at=T0,
            ended_at=T0.replace(minute=-1) if T0.minute > 0 else T0.replace(hour=T0.hour - 1),
        )


def test_token_total_must_cover_components() -> None:
    with pytest.raises(ValidationError):
        TokenBreakdown(input=10, output=10, total=5)


def test_token_total_can_exceed_sum_for_provider_quirks() -> None:
    # Provider may add headers or sum differently — selfeval accepts >=.
    tb = TokenBreakdown(input=10, output=5, reasoning=3, total=20)
    assert tb.total == 20


def test_span_discriminator_picks_subclass() -> None:
    span_payloads = [
        {
            "kind": "agent_turn",
            "id": "sp_0",
            "name": "turn",
            "started_at": T0.isoformat(),
        },
        {
            "kind": "llm_call",
            "id": "sp_1",
            "parent_id": "sp_0",
            "name": "model",
            "started_at": T0.isoformat(),
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "output": {
                "stop_reason": "tool_use",
                "tool_use_requested": [{"tool": "search", "tool_use_id": "toolu_01"}],
            },
        },
        {
            "kind": "tool_call",
            "id": "sp_2",
            "parent_id": "sp_1",
            "name": "search",
            "started_at": T0.isoformat(),
            "tool_name": "search",
            "tool_use_id": "toolu_01",
        },
    ]
    t = _trace(spans=span_payloads)
    assert isinstance(t.spans[0], AgentTurnSpan)
    assert isinstance(t.spans[1], LLMCallSpan)
    assert isinstance(t.spans[2], ToolCallSpan)


def test_span_ids_must_be_unique() -> None:
    spans = [
        AgentTurnSpan(id="sp_x", name="a", started_at=T0),
        AgentTurnSpan(id="sp_x", name="b", started_at=T0),
    ]
    with pytest.raises(ValidationError, match="span ids must be unique"):
        _trace(spans=spans)


def test_parent_id_must_reference_existing_span() -> None:
    spans = [AgentTurnSpan(id="sp_a", parent_id="sp_nonexistent", name="x", started_at=T0)]
    with pytest.raises(ValidationError, match="does not exist"):
        _trace(spans=spans)


def test_tool_use_id_must_be_requested_by_llm_span() -> None:
    spans = [
        AgentTurnSpan(id="sp_0", name="turn", started_at=T0),
        ToolCallSpan(
            id="sp_2",
            name="search",
            started_at=T0,
            tool_name="search",
            tool_use_id="toolu_orphan",
        ),
    ]
    with pytest.raises(ValidationError, match="toolu_orphan"):
        _trace(spans=spans)


def test_tool_use_id_link_succeeds_when_requested() -> None:
    spans = [
        LLMCallSpan(
            id="sp_1",
            name="model",
            started_at=T0,
            provider="anthropic",
            model="claude-sonnet-4-6",
            output=LLMOutput(
                stop_reason=StopReason.TOOL_USE,
                tool_use_requested=[ToolUseRequest(tool="search", tool_use_id="toolu_01")],
            ),
        ),
        ToolCallSpan(
            id="sp_2",
            parent_id="sp_1",
            name="search",
            started_at=T0,
            tool_name="search",
            tool_use_id="toolu_01",
            status=ToolCallStatus.OK,
        ),
    ]
    t = _trace(spans=spans)
    assert len(t.spans) == 2


def test_tool_call_without_tool_use_id_skips_link_check() -> None:
    spans = [
        ToolCallSpan(
            id="sp_2",
            name="search",
            started_at=T0,
            tool_name="search",
            tool_use_id=None,
        ),
    ]
    t = _trace(spans=spans)
    assert isinstance(t.spans[0], ToolCallSpan)


def test_llm_span_with_reasoning_and_cache_breakdown() -> None:
    s = LLMCallSpan(
        id="sp_1",
        name="model",
        started_at=T0,
        provider="anthropic",
        model="claude-sonnet-4-6",
        model_version_pinned="20260116",
        reasoning=ReasoningBlock(
            available=True,
            redacted=False,
            summary_pointer="oss://reasoning/abc/summary.txt",
            full_pointer="oss://reasoning/abc/full.txt",
            thinking_tokens=1820,
            signature="sig_abc123",
        ),
        tokens=TokenBreakdown(
            input=1240,
            input_cache_read=800,
            input_cache_creation=0,
            output=412,
            reasoning=1820,
            total=4272,
        ),
        cost_usd=CostBreakdown(input=0.00372, cache_read=0.00024, output=0.00618, total=0.01014),
        cache_hit=True,
        provider_metadata={"x-anthropic-id": "req_01"},
    )
    assert s.reasoning.signature == "sig_abc123"
    assert s.tokens.input_cache_read == 800


def test_retrieval_top_k_returned_default_zero() -> None:
    s = RetrievalSpan(
        id="sp_r",
        name="search",
        started_at=T0,
        retriever="bm25",
        top_k_requested=5,
        retrieved=[RetrievedDoc(doc_id="d_1", raw_score=0.7)],
    )
    assert s.top_k_returned == 0


def test_retrieval_top_k_requested_min_one() -> None:
    with pytest.raises(ValidationError):
        RetrievalSpan(
            id="sp_r",
            name="search",
            started_at=T0,
            retriever="bm25",
            top_k_requested=0,
        )


def test_trace_links_kind_restricted() -> None:
    with pytest.raises(ValidationError):
        TraceLink(kind="random_kind", trace_id="tr_x")  # type: ignore[arg-type]


def test_guardrail_span_passed_required() -> None:
    s = GuardrailCheckSpan(
        id="sp_g",
        name="pii_check",
        started_at=T0,
        guardrail="pii_filter",
        passed=True,
    )
    assert s.passed is True


def test_error_span_payload() -> None:
    s = ErrorSpan(
        id="sp_e",
        name="model_timeout",
        started_at=T0,
        error_type="ModelTimeoutError",
        message="upstream returned 504 after 3 retries",
        recoverable=False,
    )
    assert s.recoverable is False
