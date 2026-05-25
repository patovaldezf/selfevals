from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bootstrap.schemas.enums import SpanKind, StopReason, ToolCallStatus
from bootstrap.schemas.trace import (
    AgentSnapshotRef,
    AgentTurnSpan,
    CustomSpan,
    LLMCallSpan,
    RetrievalSpan,
    RunInfo,
    ToolCallSpan,
)
from bootstrap.trace.otel_importer import import_otel_spans

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"
T0 = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC).isoformat()
T1 = datetime(2026, 5, 16, 12, 0, 1, tzinfo=UTC).isoformat()


def _imp(spans: list[dict]) -> object:
    return import_otel_spans(
        spans,
        workspace_id=WS,
        run=RunInfo(run_id="run_01"),
        agent=AgentSnapshotRef(agent_id="ag_x", agent_version=1),
    )


def test_import_empty_list_builds_completed_trace() -> None:
    trace = _imp([])
    assert trace.spans == []  # type: ignore[attr-defined]


def test_llm_span_recognized_via_gen_ai_attrs() -> None:
    trace = _imp(
        [
            {
                "span_id": "sp_1",
                "name": "model",
                "start_time": T0,
                "end_time": T1,
                "attributes": {
                    "gen_ai.system": "anthropic",
                    "gen_ai.request.model": "claude-sonnet-4-6",
                    "gen_ai.response.model": "claude-sonnet-4-6-20260116",
                    "gen_ai.usage.input_tokens": 100,
                    "gen_ai.usage.cache_read_input_tokens": 50,
                    "gen_ai.usage.output_tokens": 30,
                    "gen_ai.usage.total_tokens": 180,
                    "gen_ai.response.finish_reasons": ["tool_use"],
                    "gen_ai.request.temperature": 0.2,
                    "custom.tenant": "seals",
                },
            }
        ]
    )
    assert len(trace.spans) == 1  # type: ignore[attr-defined]
    s = trace.spans[0]  # type: ignore[attr-defined]
    assert isinstance(s, LLMCallSpan)
    assert s.provider == "anthropic"
    assert s.model == "claude-sonnet-4-6"
    assert s.model_version_pinned == "claude-sonnet-4-6-20260116"
    assert s.tokens.input == 100
    assert s.tokens.input_cache_read == 50
    assert s.tokens.output == 30
    assert s.tokens.total == 180
    assert s.output.stop_reason == StopReason.TOOL_USE
    assert s.params.get("temperature") == 0.2
    assert s.provider_metadata == {"custom.tenant": "seals"}


def test_tool_span_recognized() -> None:
    # Realistic case: an LLM call requests the tool, then a TOOL span
    # references that call_id. The schema validator requires the link.
    trace = _imp(
        [
            {
                "span_id": "sp_llm",
                "name": "model",
                "start_time": T0,
                "end_time": T1,
                "attributes": {
                    "gen_ai.system": "anthropic",
                    "gen_ai.request.model": "claude-sonnet-4-6",
                    "gen_ai.response.finish_reasons": ["tool_use"],
                },
            },
            {
                "span_id": "sp_t",
                "parent_span_id": "sp_llm",
                "name": "search",
                "start_time": T0,
                "end_time": T1,
                "attributes": {
                    "openinference.span.kind": "TOOL",
                    "tool.name": "search",
                    "tool.call_id": "toolu_xyz",
                    "tool.status": "ok",
                },
            },
        ]
    )
    # The OTel importer cannot know about LLM tool_use_requested, so the
    # importer leaves tool_use_id set; the call site is responsible for
    # populating LLMOutput.tool_use_requested if it wants strict linkage.
    tool_span = next(s for s in trace.spans if isinstance(s, ToolCallSpan))  # type: ignore[attr-defined]
    assert tool_span.tool_name == "search"
    assert tool_span.tool_use_id == "toolu_xyz"
    assert tool_span.status == ToolCallStatus.OK


def test_retrieval_span_recognized() -> None:
    trace = _imp(
        [
            {
                "span_id": "sp_r",
                "name": "rag",
                "start_time": T0,
                "end_time": T1,
                "attributes": {
                    "openinference.span.kind": "RETRIEVER",
                    "retrieval.retriever": "bm25",
                    "retrieval.documents.count": 5,
                },
            }
        ]
    )
    s = trace.spans[0]  # type: ignore[attr-defined]
    assert isinstance(s, RetrievalSpan)
    assert s.retriever == "bm25"
    assert s.top_k_requested == 5


def test_unknown_kind_becomes_custom_span() -> None:
    trace = _imp(
        [
            {
                "span_id": "sp_x",
                "name": "exotic",
                "start_time": T0,
                "end_time": T1,
                "attributes": {"foo": "bar"},
            }
        ]
    )
    s = trace.spans[0]  # type: ignore[attr-defined]
    assert isinstance(s, CustomSpan)
    assert s.payload == {"foo": "bar"}


def test_agent_chain_recognized_as_agent_turn() -> None:
    trace = _imp(
        [
            {
                "span_id": "sp_a",
                "name": "chain",
                "start_time": T0,
                "end_time": T1,
                "attributes": {"openinference.span.kind": "CHAIN"},
            }
        ]
    )
    assert isinstance(trace.spans[0], AgentTurnSpan)  # type: ignore[attr-defined]


def test_parent_child_preserved() -> None:
    trace = _imp(
        [
            {
                "span_id": "sp_a",
                "name": "turn",
                "start_time": T0,
                "end_time": T1,
                "attributes": {"openinference.span.kind": "AGENT"},
            },
            {
                "span_id": "sp_b",
                "parent_span_id": "sp_a",
                "name": "model",
                "start_time": T0,
                "end_time": T1,
                "attributes": {"gen_ai.system": "anthropic", "gen_ai.request.model": "x"},
            },
        ]
    )
    spans = trace.spans  # type: ignore[attr-defined]
    by_id = {s.id: s for s in spans}
    assert by_id["sp_b"].parent_id == "sp_a"


def test_unrecognized_finish_reason_returns_none() -> None:
    trace = _imp(
        [
            {
                "span_id": "sp_1",
                "name": "model",
                "start_time": T0,
                "end_time": T1,
                "attributes": {
                    "gen_ai.system": "anthropic",
                    "gen_ai.request.model": "claude-sonnet-4-6",
                    "gen_ai.response.finish_reasons": ["abracadabra"],
                },
            }
        ]
    )
    s = trace.spans[0]  # type: ignore[attr-defined]
    assert isinstance(s, LLMCallSpan)
    assert s.output.stop_reason is None


def test_epoch_seconds_and_nanos_both_parsed() -> None:
    trace = _imp(
        [
            {
                "span_id": "sp_a",
                "name": "x",
                "start_time": 1716115200,
                "duration_ms": 1500,
                "attributes": {"foo": "bar"},
            },
            {
                "span_id": "sp_b",
                "name": "x",
                "start_time": 1716115200000000000,
                "duration_ms": 700,
                "attributes": {"foo": "bar"},
            },
        ]
    )
    spans = trace.spans  # type: ignore[attr-defined]
    # Same instant, encoded differently.
    assert spans[0].started_at == spans[1].started_at


def test_invalid_workspace_rejected() -> None:
    with pytest.raises(ValueError):
        import_otel_spans(
            [],
            workspace_id="",
            run=RunInfo(run_id="r"),
            agent=AgentSnapshotRef(agent_id="ag_x", agent_version=1),
        )


def test_classification_falls_back_to_kind_field() -> None:
    trace = _imp(
        [
            {
                "span_id": "sp_1",
                "name": "x",
                "kind": "tool",
                "start_time": T0,
                "end_time": T1,
                "attributes": {"tool.name": "search"},
            },
        ]
    )
    assert isinstance(trace.spans[0], ToolCallSpan)  # type: ignore[attr-defined]
    # Sanity: kind enum membership inferred.
    assert SpanKind.TOOL_CALL == "tool_call"
