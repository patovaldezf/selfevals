"""Insert one span (base row + kind-specific detail table) for `TraceMapper.upsert`."""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from selfevals.schemas.trace import (
    CustomSpan,
    DecisionSpan,
    ErrorSpan,
    GuardrailCheckSpan,
    HandoffSpan,
    HumanInterventionSpan,
    LLMCallSpan,
    MemoryReadSpan,
    MemoryWriteSpan,
    RetrievalSpan,
    Span,
    SttSpan,
    ToolCallSpan,
    TtsSpan,
)


def insert_span(cur: Any, trace_id: str, workspace_id: str, index: int, span: Span) -> None:
    cur.execute(
        """
        INSERT INTO trace_spans
          (span_id, trace_id, workspace_id, span_index, kind, parent_id, name,
           started_at, duration_ms)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            span.id,
            trace_id,
            workspace_id,
            index,
            span.kind.value,
            span.parent_id,
            span.name,
            span.started_at,
            span.duration_ms,
        ),
    )
    if isinstance(span, LLMCallSpan):
        _insert_llm_span(cur, trace_id, workspace_id, span)
    elif isinstance(span, ToolCallSpan):
        _insert_tool_span(cur, trace_id, workspace_id, span)
    elif isinstance(span, RetrievalSpan):
        _insert_retrieval_span(cur, trace_id, span)
    elif isinstance(span, MemoryReadSpan):
        _insert_memory_read_span(cur, trace_id, span)
    elif isinstance(span, MemoryWriteSpan):
        _insert_memory_write_span(cur, trace_id, span)
    elif isinstance(span, DecisionSpan):
        _insert_decision_span(cur, trace_id, span)
    elif isinstance(span, HandoffSpan):
        _insert_handoff_span(cur, trace_id, span)
    elif isinstance(span, HumanInterventionSpan):
        _insert_human_intervention_span(cur, trace_id, span)
    elif isinstance(span, GuardrailCheckSpan):
        _insert_guardrail_check_span(cur, trace_id, span)
    elif isinstance(span, SttSpan):
        _insert_stt_span(cur, trace_id, span)
    elif isinstance(span, TtsSpan):
        _insert_tts_span(cur, trace_id, span)
    elif isinstance(span, ErrorSpan):
        _insert_error_span(cur, trace_id, span)
    elif isinstance(span, CustomSpan):
        _insert_custom_span(cur, trace_id, span)
    # AgentTurnSpan: no detail table.


def _insert_llm_span(cur: Any, trace_id: str, workspace_id: str, span: LLMCallSpan) -> None:
    cur.execute(
        """
        INSERT INTO trace_llm_calls
          (span_id, trace_id, workspace_id, provider, model, model_version_pinned,
           system_prompt_pointer, system_prompt_hash, system_prompt_inline,
           messages_pointer, messages_hash, messages_inline, tools_offered,
           tools_offered_hash, params, reasoning_available, reasoning_redacted,
           reasoning_summary_pointer, reasoning_full_pointer, reasoning_thinking_tokens,
           reasoning_signature, output_content_pointer, output_content_hash,
           output_content_inline, output_stop_reason, tokens_input,
           tokens_input_cache_read, tokens_input_cache_creation, tokens_output,
           tokens_reasoning, tokens_total, cost_input, cost_cache_read,
           cost_cache_creation, cost_output, cost_total, time_to_first_token_ms,
           tokens_per_second, retries, cache_hit, provider_metadata)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            span.id,
            trace_id,
            workspace_id,
            span.provider,
            span.model,
            span.model_version_pinned,
            span.system_prompt_pointer,
            span.system_prompt_hash,
            span.system_prompt_inline,
            span.messages_pointer,
            span.messages_hash,
            span.messages_inline,
            list(span.tools_offered),
            span.tools_offered_hash,
            Jsonb(span.params),
            span.reasoning.available,
            span.reasoning.redacted,
            span.reasoning.summary_pointer,
            span.reasoning.full_pointer,
            span.reasoning.thinking_tokens,
            span.reasoning.signature,
            span.output.content_pointer,
            span.output.content_hash,
            span.output.content_inline,
            span.output.stop_reason.value if span.output.stop_reason else None,
            span.tokens.input,
            span.tokens.input_cache_read,
            span.tokens.input_cache_creation,
            span.tokens.output,
            span.tokens.reasoning,
            span.tokens.total,
            span.cost_usd.input,
            span.cost_usd.cache_read,
            span.cost_usd.cache_creation,
            span.cost_usd.output,
            span.cost_usd.total,
            span.time_to_first_token_ms,
            span.tokens_per_second,
            span.retries,
            span.cache_hit,
            Jsonb(span.provider_metadata),
        ),
    )
    for pos, req in enumerate(span.output.tool_use_requested):
        cur.execute(
            "INSERT INTO trace_llm_tool_requests "
            "(trace_id, span_id, position, tool, tool_use_id) "
            "VALUES (%s, %s, %s, %s, %s)",
            (trace_id, span.id, pos, req.tool, req.tool_use_id),
        )


def _insert_tool_span(cur: Any, trace_id: str, workspace_id: str, span: ToolCallSpan) -> None:
    cur.execute(
        """
        INSERT INTO trace_tool_calls
          (span_id, trace_id, workspace_id, tool_name, tool_version, tool_use_id,
           args_pointer, args_hash, args_inline, result_pointer, result_hash,
           result_inline, status, error, retry_chain, sandboxed, side_effects)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            span.id,
            trace_id,
            workspace_id,
            span.tool_name,
            span.tool_version,
            span.tool_use_id,
            span.args_pointer,
            span.args_hash,
            span.args_inline,
            span.result_pointer,
            span.result_hash,
            span.result_inline,
            span.status.value,
            span.error,
            list(span.retry_chain),
            span.sandboxed,
            Jsonb(span.side_effects),
        ),
    )


def _insert_retrieval_span(cur: Any, trace_id: str, span: RetrievalSpan) -> None:
    cur.execute(
        """
        INSERT INTO trace_retrieval_spans
          (trace_id, span_id, retriever, query_pointer, query_hash,
           query_embedding_model, top_k_requested, top_k_returned, reranker,
           grounding_used)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            trace_id,
            span.id,
            span.retriever,
            span.query_pointer,
            span.query_hash,
            span.query_embedding_model,
            span.top_k_requested,
            span.top_k_returned,
            span.reranker,
            list(span.grounding_used),
        ),
    )
    for pos, doc in enumerate(span.retrieved):
        cur.execute(
            """
            INSERT INTO trace_retrieved_docs
              (trace_id, span_id, position, doc_id, doc_version, chunk_id,
               raw_score, rerank_score)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                trace_id,
                span.id,
                pos,
                doc.doc_id,
                doc.doc_version,
                doc.chunk_id,
                doc.raw_score,
                doc.rerank_score,
            ),
        )


def _insert_memory_read_span(cur: Any, trace_id: str, span: MemoryReadSpan) -> None:
    cur.execute(
        """
        INSERT INTO trace_memory_read_spans
          (trace_id, span_id, memory_store, keys_requested, keys_hit, keys_missed,
           values_pointer)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            trace_id,
            span.id,
            span.memory_store,
            list(span.keys_requested),
            list(span.keys_hit),
            list(span.keys_missed),
            span.values_pointer,
        ),
    )


def _insert_memory_write_span(cur: Any, trace_id: str, span: MemoryWriteSpan) -> None:
    cur.execute(
        "INSERT INTO trace_memory_write_spans "
        "(trace_id, span_id, memory_store, keys_written, values_pointer) "
        "VALUES (%s, %s, %s, %s, %s)",
        (trace_id, span.id, span.memory_store, list(span.keys_written), span.values_pointer),
    )


def _insert_decision_span(cur: Any, trace_id: str, span: DecisionSpan) -> None:
    cur.execute(
        """
        INSERT INTO trace_decision_spans
          (trace_id, span_id, decision_type, chosen, alternatives_considered,
           rationale_pointer, confidence)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            trace_id,
            span.id,
            span.decision_type,
            span.chosen,
            list(span.alternatives_considered),
            span.rationale_pointer,
            span.confidence,
        ),
    )


def _insert_handoff_span(cur: Any, trace_id: str, span: HandoffSpan) -> None:
    cur.execute(
        "INSERT INTO trace_handoff_spans (trace_id, span_id, target, payload_pointer) "
        "VALUES (%s, %s, %s, %s)",
        (trace_id, span.id, span.target, span.payload_pointer),
    )


def _insert_human_intervention_span(cur: Any, trace_id: str, span: HumanInterventionSpan) -> None:
    cur.execute(
        "INSERT INTO trace_human_intervention_spans "
        "(trace_id, span_id, actor, action, rationale_pointer) VALUES (%s, %s, %s, %s, %s)",
        (trace_id, span.id, span.actor, span.action, span.rationale_pointer),
    )


def _insert_guardrail_check_span(cur: Any, trace_id: str, span: GuardrailCheckSpan) -> None:
    cur.execute(
        "INSERT INTO trace_guardrail_check_spans "
        "(trace_id, span_id, guardrail, passed, detail_pointer) VALUES (%s, %s, %s, %s, %s)",
        (trace_id, span.id, span.guardrail, span.passed, span.detail_pointer),
    )


def _insert_error_span(cur: Any, trace_id: str, span: ErrorSpan) -> None:
    cur.execute(
        "INSERT INTO trace_error_spans "
        "(trace_id, span_id, error_type, message, recoverable) "
        "VALUES (%s, %s, %s, %s, %s)",
        (trace_id, span.id, span.error_type, span.message, span.recoverable),
    )


def _insert_custom_span(cur: Any, trace_id: str, span: CustomSpan) -> None:
    cur.execute(
        "INSERT INTO trace_custom_spans (trace_id, span_id, payload) VALUES (%s, %s, %s)",
        (trace_id, span.id, Jsonb(span.payload)),
    )


def _insert_stt_span(cur: Any, trace_id: str, span: SttSpan) -> None:
    cur.execute(
        """
        INSERT INTO trace_stt_spans
          (trace_id, span_id, provider, model, audio_pointer, audio_hash,
           audio_duration_ms, audio_mime_type, transcript_pointer, transcript_hash,
           transcript_inline, language, confidence, streaming,
           time_to_first_transcript_ms, provider_metadata)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            trace_id,
            span.id,
            span.provider,
            span.model,
            span.audio_pointer,
            span.audio_hash,
            span.audio_duration_ms,
            span.audio_mime_type,
            span.transcript_pointer,
            span.transcript_hash,
            span.transcript_inline,
            span.language,
            span.confidence,
            span.streaming,
            span.time_to_first_transcript_ms,
            Jsonb(span.provider_metadata),
        ),
    )


def _insert_tts_span(cur: Any, trace_id: str, span: TtsSpan) -> None:
    cur.execute(
        """
        INSERT INTO trace_tts_spans
          (trace_id, span_id, provider, model, voice_id, text_pointer, text_hash,
           text_inline, audio_pointer, audio_hash, audio_duration_ms,
           audio_mime_type, time_to_first_byte_ms, provider_metadata)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            trace_id,
            span.id,
            span.provider,
            span.model,
            span.voice_id,
            span.text_pointer,
            span.text_hash,
            span.text_inline,
            span.audio_pointer,
            span.audio_hash,
            span.audio_duration_ms,
            span.audio_mime_type,
            span.time_to_first_byte_ms,
            Jsonb(span.provider_metadata),
        ),
    )
