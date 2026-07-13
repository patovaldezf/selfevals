"""Rebuild spans (base row + kind-specific detail table) for `TraceMapper.load`."""

from __future__ import annotations

from typing import Any

from selfevals.schemas.trace import (
    AgentTurnSpan,
    CostBreakdown,
    CustomSpan,
    DecisionSpan,
    ErrorSpan,
    GuardrailCheckSpan,
    HandoffSpan,
    HumanInterventionSpan,
    LLMCallSpan,
    LLMOutput,
    MemoryReadSpan,
    MemoryWriteSpan,
    ReasoningBlock,
    RetrievalSpan,
    RetrievedDoc,
    Span,
    TokenBreakdown,
    ToolCallSpan,
    ToolUseRequest,
)


def load_spans(cur: Any, trace_id: str) -> list[Span]:
    cur.execute(
        """
        SELECT span_id, kind, parent_id, name, started_at, duration_ms
        FROM trace_spans WHERE trace_id = %s ORDER BY span_index
        """,
        (trace_id,),
    )
    base_rows = cur.fetchall()
    spans: list[Span] = []
    for span_id, kind, parent_id, name, started_at, duration_ms in base_rows:
        base = {
            "id": span_id,
            "parent_id": parent_id,
            "name": name,
            "started_at": started_at,
            "duration_ms": duration_ms,
        }
        spans.append(build_span(cur, trace_id, kind, span_id, base))
    return spans


def build_span(cur: Any, trace_id: str, kind: str, span_id: str, base: dict[str, Any]) -> Span:
    if kind == "agent_turn":
        return AgentTurnSpan(**base)
    if kind == "llm_call":
        return _build_llm_span(cur, trace_id, span_id, base)
    if kind == "tool_call":
        return _build_tool_span(cur, trace_id, span_id, base)
    if kind == "retrieval":
        return _build_retrieval_span(cur, trace_id, span_id, base)
    if kind == "memory_read":
        cur.execute(
            "SELECT memory_store, keys_requested, keys_hit, keys_missed, values_pointer "
            "FROM trace_memory_read_spans WHERE trace_id = %s AND span_id = %s",
            (trace_id, span_id),
        )
        store, req, hit, missed, vp = cur.fetchone()
        return MemoryReadSpan(
            **base,
            memory_store=store,
            keys_requested=req,
            keys_hit=hit,
            keys_missed=missed,
            values_pointer=vp,
        )
    if kind == "memory_write":
        cur.execute(
            "SELECT memory_store, keys_written, values_pointer "
            "FROM trace_memory_write_spans WHERE trace_id = %s AND span_id = %s",
            (trace_id, span_id),
        )
        store, written, vp = cur.fetchone()
        return MemoryWriteSpan(**base, memory_store=store, keys_written=written, values_pointer=vp)
    if kind == "decision":
        cur.execute(
            "SELECT decision_type, chosen, alternatives_considered, rationale_pointer, "
            "confidence FROM trace_decision_spans WHERE trace_id = %s AND span_id = %s",
            (trace_id, span_id),
        )
        dt, chosen, alts, rp, conf = cur.fetchone()
        return DecisionSpan(
            **base,
            decision_type=dt,
            chosen=chosen,
            alternatives_considered=alts,
            rationale_pointer=rp,
            confidence=conf,
        )
    if kind == "handoff":
        cur.execute(
            "SELECT target, payload_pointer FROM trace_handoff_spans "
            "WHERE trace_id = %s AND span_id = %s",
            (trace_id, span_id),
        )
        target, pp = cur.fetchone()
        return HandoffSpan(**base, target=target, payload_pointer=pp)
    if kind == "human_intervention":
        cur.execute(
            "SELECT actor, action, rationale_pointer "
            "FROM trace_human_intervention_spans WHERE trace_id = %s AND span_id = %s",
            (trace_id, span_id),
        )
        actor, action, rp = cur.fetchone()
        return HumanInterventionSpan(**base, actor=actor, action=action, rationale_pointer=rp)
    if kind == "guardrail_check":
        cur.execute(
            "SELECT guardrail, passed, detail_pointer "
            "FROM trace_guardrail_check_spans WHERE trace_id = %s AND span_id = %s",
            (trace_id, span_id),
        )
        guardrail, passed, dp = cur.fetchone()
        return GuardrailCheckSpan(**base, guardrail=guardrail, passed=passed, detail_pointer=dp)
    if kind == "error":
        cur.execute(
            "SELECT error_type, message, recoverable FROM trace_error_spans "
            "WHERE trace_id = %s AND span_id = %s",
            (trace_id, span_id),
        )
        et, msg, rec = cur.fetchone()
        return ErrorSpan(**base, error_type=et, message=msg, recoverable=rec)
    if kind == "custom":
        cur.execute(
            "SELECT payload FROM trace_custom_spans WHERE trace_id = %s AND span_id = %s",
            (trace_id, span_id),
        )
        (payload,) = cur.fetchone()
        return CustomSpan(**base, payload=payload)
    raise ValueError(f"unknown span kind {kind!r}")


def _build_llm_span(cur: Any, trace_id: str, span_id: str, base: dict[str, Any]) -> LLMCallSpan:
    cur.execute(
        """
        SELECT provider, model, model_version_pinned, system_prompt_pointer,
               system_prompt_hash, system_prompt_inline, messages_pointer, messages_hash,
               messages_inline, tools_offered, tools_offered_hash, params,
               reasoning_available, reasoning_redacted, reasoning_summary_pointer,
               reasoning_full_pointer, reasoning_thinking_tokens, reasoning_signature,
               output_content_pointer, output_content_hash, output_content_inline,
               output_stop_reason, tokens_input, tokens_input_cache_read,
               tokens_input_cache_creation, tokens_output, tokens_reasoning, tokens_total,
               cost_input, cost_cache_read, cost_cache_creation, cost_output, cost_total,
               time_to_first_token_ms, tokens_per_second, retries, cache_hit, provider_metadata
        FROM trace_llm_calls WHERE trace_id = %s AND span_id = %s
        """,
        (trace_id, span_id),
    )
    r = cur.fetchone()
    cur.execute(
        "SELECT tool, tool_use_id FROM trace_llm_tool_requests "
        "WHERE trace_id = %s AND span_id = %s ORDER BY position",
        (trace_id, span_id),
    )
    tool_use_requested = [ToolUseRequest(tool=t, tool_use_id=tid) for t, tid in cur.fetchall()]
    return LLMCallSpan(
        **base,
        provider=r[0],
        model=r[1],
        model_version_pinned=r[2],
        system_prompt_pointer=r[3],
        system_prompt_hash=r[4],
        system_prompt_inline=r[5],
        messages_pointer=r[6],
        messages_hash=r[7],
        messages_inline=r[8],
        tools_offered=r[9],
        tools_offered_hash=r[10],
        params=r[11],
        reasoning=ReasoningBlock(
            available=r[12],
            redacted=r[13],
            summary_pointer=r[14],
            full_pointer=r[15],
            thinking_tokens=r[16],
            signature=r[17],
        ),
        output=LLMOutput(
            content_pointer=r[18],
            content_hash=r[19],
            content_inline=r[20],
            stop_reason=r[21],
            tool_use_requested=tool_use_requested,
        ),
        tokens=TokenBreakdown(
            input=r[22],
            input_cache_read=r[23],
            input_cache_creation=r[24],
            output=r[25],
            reasoning=r[26],
            total=r[27],
        ),
        cost_usd=CostBreakdown(
            input=r[28],
            cache_read=r[29],
            cache_creation=r[30],
            output=r[31],
            total=r[32],
        ),
        time_to_first_token_ms=r[33],
        tokens_per_second=r[34],
        retries=r[35],
        cache_hit=r[36],
        provider_metadata=r[37],
    )


def _build_tool_span(cur: Any, trace_id: str, span_id: str, base: dict[str, Any]) -> ToolCallSpan:
    cur.execute(
        """
        SELECT tool_name, tool_version, tool_use_id, args_pointer, args_hash,
               args_inline, result_pointer, result_hash, result_inline, status,
               error, retry_chain, sandboxed, side_effects
        FROM trace_tool_calls WHERE trace_id = %s AND span_id = %s
        """,
        (trace_id, span_id),
    )
    r = cur.fetchone()
    return ToolCallSpan(
        **base,
        tool_name=r[0],
        tool_version=r[1],
        tool_use_id=r[2],
        args_pointer=r[3],
        args_hash=r[4],
        args_inline=r[5],
        result_pointer=r[6],
        result_hash=r[7],
        result_inline=r[8],
        status=r[9],
        error=r[10],
        retry_chain=r[11],
        sandboxed=r[12],
        side_effects=r[13],
    )


def _build_retrieval_span(
    cur: Any, trace_id: str, span_id: str, base: dict[str, Any]
) -> RetrievalSpan:
    cur.execute(
        """
        SELECT retriever, query_pointer, query_hash, query_embedding_model,
               top_k_requested, top_k_returned, reranker, grounding_used
        FROM trace_retrieval_spans WHERE trace_id = %s AND span_id = %s
        """,
        (trace_id, span_id),
    )
    r = cur.fetchone()
    cur.execute(
        "SELECT doc_id, doc_version, chunk_id, raw_score, rerank_score "
        "FROM trace_retrieved_docs WHERE trace_id = %s AND span_id = %s ORDER BY position",
        (trace_id, span_id),
    )
    retrieved = [
        RetrievedDoc(
            doc_id=doc_id,
            doc_version=dv,
            chunk_id=cid,
            raw_score=rs,
            rerank_score=rr,
        )
        for doc_id, dv, cid, rs, rr in cur.fetchall()
    ]
    return RetrievalSpan(
        **base,
        retriever=r[0],
        query_pointer=r[1],
        query_hash=r[2],
        query_embedding_model=r[3],
        top_k_requested=r[4],
        top_k_returned=r[5],
        reranker=r[6],
        retrieved=retrieved,
        grounding_used=r[7],
    )
