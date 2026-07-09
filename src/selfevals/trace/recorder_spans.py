"""Free functions that build the simple, single-call span types for `TraceRecorder`.

Split out of `recorder.py` (same free-function-delegate pattern as
`storage/postgres/mappers/trace_spans_write.py`): each `TraceRecorder.add_*`
method is one line delegating here — a pure function of the span's fields
plus `(span_id, parent_id, started_at)`, no `self`, no timing (these spans
are instantaneous facts, not durations).
"""

from __future__ import annotations

from datetime import datetime

from selfevals.schemas.trace import (
    DecisionSpan,
    ErrorSpan,
    GuardrailCheckSpan,
    HandoffSpan,
    HumanInterventionSpan,
    MemoryReadSpan,
    MemoryWriteSpan,
    RetrievalSpan,
    RetrievedDoc,
)


def build_retrieval_span(
    *,
    span_id: str,
    parent_id: str | None,
    name: str,
    started_at: datetime,
    retriever: str,
    top_k_requested: int,
    retrieved: list[RetrievedDoc] | None,
    reranker: str | None,
    query_pointer: str | None,
    query_hash: str | None,
) -> RetrievalSpan:
    retrieved = retrieved or []
    return RetrievalSpan(
        id=span_id,
        parent_id=parent_id,
        name=name,
        started_at=started_at,
        retriever=retriever,
        top_k_requested=top_k_requested,
        top_k_returned=len(retrieved),
        retrieved=retrieved,
        reranker=reranker,
        query_pointer=query_pointer,
        query_hash=query_hash,
    )


def build_memory_read_span(
    *,
    span_id: str,
    parent_id: str | None,
    name: str,
    started_at: datetime,
    store: str,
    hits: list[str],
    misses: list[str],
) -> MemoryReadSpan:
    return MemoryReadSpan(
        id=span_id,
        parent_id=parent_id,
        name=name,
        started_at=started_at,
        memory_store=store,
        keys_hit=hits,
        keys_missed=misses,
    )


def build_memory_write_span(
    *,
    span_id: str,
    parent_id: str | None,
    name: str,
    started_at: datetime,
    store: str,
    keys: list[str],
) -> MemoryWriteSpan:
    return MemoryWriteSpan(
        id=span_id,
        parent_id=parent_id,
        name=name,
        started_at=started_at,
        memory_store=store,
        keys_written=keys,
    )


def build_decision_span(
    *,
    span_id: str,
    parent_id: str | None,
    name: str,
    started_at: datetime,
    decision_type: str,
    chosen: str,
    alternatives: list[str] | None,
    confidence: float | None,
) -> DecisionSpan:
    return DecisionSpan(
        id=span_id,
        parent_id=parent_id,
        name=name,
        started_at=started_at,
        decision_type=decision_type,
        chosen=chosen,
        alternatives_considered=alternatives or [],
        confidence=confidence,
    )


def build_handoff_span(
    *, span_id: str, parent_id: str | None, name: str, started_at: datetime, target: str
) -> HandoffSpan:
    return HandoffSpan(
        id=span_id, parent_id=parent_id, name=name, started_at=started_at, target=target
    )


def build_human_intervention_span(
    *,
    span_id: str,
    parent_id: str | None,
    name: str,
    started_at: datetime,
    actor: str,
    action: str,
) -> HumanInterventionSpan:
    return HumanInterventionSpan(
        id=span_id,
        parent_id=parent_id,
        name=name,
        started_at=started_at,
        actor=actor,
        action=action,
    )


def build_guardrail_check_span(
    *,
    span_id: str,
    parent_id: str | None,
    name: str,
    started_at: datetime,
    guardrail: str,
    passed: bool,
) -> GuardrailCheckSpan:
    return GuardrailCheckSpan(
        id=span_id,
        parent_id=parent_id,
        name=name,
        started_at=started_at,
        guardrail=guardrail,
        passed=passed,
    )


def build_error_span(
    *,
    span_id: str,
    parent_id: str | None,
    name: str,
    started_at: datetime,
    error_type: str,
    message: str,
    recoverable: bool,
) -> ErrorSpan:
    return ErrorSpan(
        id=span_id,
        parent_id=parent_id,
        name=name,
        started_at=started_at,
        error_type=error_type,
        message=message,
        recoverable=recoverable,
    )
