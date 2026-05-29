from __future__ import annotations

from datetime import UTC, datetime

import pytest

from selfevals.schemas.enums import SpanKind, StopReason, ToolCallStatus
from selfevals.schemas.trace import (
    AgentSnapshotRef,
    AgentTurnSpan,
    CustomSpan,
    LLMCallSpan,
    RetrievalSpan,
    RunInfo,
    ToolCallSpan,
)
from selfevals.trace.otel_importer import import_otel_spans

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
                    "custom.tenant": "acme",
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
    assert s.provider_metadata == {"custom.tenant": "acme"}


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


def test_messages_extracted_via_openinference_native_attrs() -> None:
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
                    "llm.input_messages.0.message.role": "system",
                    "llm.input_messages.0.message.content": "You are helpful.",
                    "llm.input_messages.1.message.role": "user",
                    "llm.input_messages.1.message.content": "Hola",
                    "llm.output_messages.0.message.role": "assistant",
                    "llm.output_messages.0.message.content": "¡Hola! ¿Cómo ayudo?",
                },
            }
        ]
    )
    s = trace.spans[0]  # type: ignore[attr-defined]
    assert isinstance(s, LLMCallSpan)
    msgs_in = s.provider_metadata["selfevals.messages_in"]
    assert msgs_in == [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Hola"},
    ]
    msgs_out = s.provider_metadata["selfevals.messages_out"]
    assert msgs_out == [{"role": "assistant", "content": "¡Hola! ¿Cómo ayudo?"}]
    # Hashes are always set when messages exist, for dedup / drift detection.
    assert s.messages_hash is not None and s.messages_hash.startswith("sha256:")
    assert s.output.content_hash is not None and s.output.content_hash.startswith("sha256:")
    # Raw flattened keys must not leak into metadata alongside the structured form.
    assert not any(k.startswith("llm.input_messages.") for k in s.provider_metadata)
    assert not any(k.startswith("llm.output_messages.") for k in s.provider_metadata)


def test_messages_extracted_via_gen_ai_alias_attrs() -> None:
    trace = _imp(
        [
            {
                "span_id": "sp_1",
                "name": "model",
                "start_time": T0,
                "end_time": T1,
                "attributes": {
                    "gen_ai.system": "openai",
                    "gen_ai.request.model": "gpt-4o",
                    "gen_ai.prompt.0.role": "user",
                    "gen_ai.prompt.0.content": "What is 2+2?",
                    "gen_ai.completion.0.role": "assistant",
                    "gen_ai.completion.0.content": "4",
                },
            }
        ]
    )
    s = trace.spans[0]  # type: ignore[attr-defined]
    assert isinstance(s, LLMCallSpan)
    assert s.provider_metadata["selfevals.messages_in"] == [
        {"role": "user", "content": "What is 2+2?"}
    ]
    assert s.provider_metadata["selfevals.messages_out"] == [
        {"role": "assistant", "content": "4"}
    ]


def test_message_index_order_is_numeric_not_lexical() -> None:
    # Indices 0,1,...,10,11 must sort numerically (10 after 9), not as strings.
    attrs = {"gen_ai.system": "anthropic", "gen_ai.request.model": "x"}
    for i in range(12):
        attrs[f"gen_ai.prompt.{i}.role"] = "user"
        attrs[f"gen_ai.prompt.{i}.content"] = f"msg{i}"
    trace = _imp(
        [{"span_id": "sp_1", "name": "model", "start_time": T0, "end_time": T1, "attributes": attrs}]
    )
    s = trace.spans[0]  # type: ignore[attr-defined]
    contents = [m["content"] for m in s.provider_metadata["selfevals.messages_in"]]
    assert contents == [f"msg{i}" for i in range(12)]


def test_openinference_native_wins_when_both_families_present() -> None:
    trace = _imp(
        [
            {
                "span_id": "sp_1",
                "name": "model",
                "start_time": T0,
                "end_time": T1,
                "attributes": {
                    "gen_ai.system": "anthropic",
                    "gen_ai.request.model": "x",
                    "llm.input_messages.0.message.role": "user",
                    "llm.input_messages.0.message.content": "native",
                    "gen_ai.prompt.0.role": "user",
                    "gen_ai.prompt.0.content": "alias",
                },
            }
        ]
    )
    s = trace.spans[0]  # type: ignore[attr-defined]
    assert s.provider_metadata["selfevals.messages_in"] == [{"role": "user", "content": "native"}]


def test_no_messages_leaves_hashes_none() -> None:
    trace = _imp(
        [
            {
                "span_id": "sp_1",
                "name": "model",
                "start_time": T0,
                "end_time": T1,
                "attributes": {"gen_ai.system": "anthropic", "gen_ai.request.model": "x"},
            }
        ]
    )
    s = trace.spans[0]  # type: ignore[attr-defined]
    assert s.messages_hash is None
    assert s.output.content_hash is None
    assert "selfevals.messages_in" not in s.provider_metadata
    assert "selfevals.messages_out" not in s.provider_metadata


def test_thread_id_detected_from_openinference_session_id() -> None:
    trace = _imp(
        [
            {
                "span_id": "sp_1",
                "name": "model",
                "start_time": T0,
                "end_time": T1,
                "attributes": {
                    "gen_ai.system": "anthropic",
                    "gen_ai.request.model": "x",
                    "session.id": "sess_abc",
                },
            }
        ]
    )
    assert trace.run.thread_id == "sess_abc"  # type: ignore[attr-defined]


def test_thread_id_detected_from_gen_ai_conversation_id() -> None:
    trace = _imp(
        [
            {
                "span_id": "sp_1",
                "name": "model",
                "start_time": T0,
                "end_time": T1,
                "attributes": {
                    "gen_ai.system": "openai",
                    "gen_ai.request.model": "x",
                    "gen_ai.conversation.id": "conv_42",
                },
            }
        ]
    )
    assert trace.run.thread_id == "conv_42"  # type: ignore[attr-defined]


def test_explicit_thread_id_on_run_is_not_overwritten() -> None:
    trace = import_otel_spans(
        [
            {
                "span_id": "sp_1",
                "name": "model",
                "start_time": T0,
                "end_time": T1,
                "attributes": {
                    "gen_ai.system": "anthropic",
                    "gen_ai.request.model": "x",
                    "session.id": "from_span",
                },
            }
        ],
        workspace_id=WS,
        run=RunInfo(run_id="run_01", thread_id="from_caller"),
        agent=AgentSnapshotRef(agent_id="ag_x", agent_version=1),
    )
    assert trace.run.thread_id == "from_caller"


def test_no_session_attr_leaves_thread_id_none() -> None:
    trace = _imp(
        [
            {
                "span_id": "sp_1",
                "name": "model",
                "start_time": T0,
                "end_time": T1,
                "attributes": {"gen_ai.system": "anthropic", "gen_ai.request.model": "x"},
            }
        ]
    )
    assert trace.run.thread_id is None  # type: ignore[attr-defined]


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
