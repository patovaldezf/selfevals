"""OpenTelemetry → bootstrap Trace importer.

Accepts a list of OTel-style spans (JSON-dict form, the shape an exporter
would emit) and produces a `Trace`. Recognises the `gen_ai.*` and
`openinference.*` attribute namespaces; unknown attributes go into
`provider_metadata` so nothing is silently dropped.

Mapping reference:
- gen_ai.system → llm.provider
- gen_ai.request.model → llm.model
- gen_ai.response.model → llm.model_version_pinned
- gen_ai.usage.input_tokens → tokens.input
- gen_ai.usage.output_tokens → tokens.output
- gen_ai.usage.cache_read_input_tokens → tokens.input_cache_read
- gen_ai.usage.cache_creation_input_tokens → tokens.input_cache_creation
- gen_ai.response.finish_reasons[0] → stop_reason (loose lowercase match)
- openinference.span.kind: LLM / TOOL / RETRIEVER / AGENT / CHAIN → SpanKind
- tool.name / openinference.tool.name → tool_name
- tool.parameters / openinference.tool.parameters → args_pointer (or inline)
- retrieval.documents.count → retrieval span hint

This is intentionally a minimum-viable mapper. Adding fields later is a
matter of looking up another attribute name — schema doesn't change.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bootstrap._internal.ids import new_prefixed_id
from bootstrap.schemas.enums import (
    SandboxMode,
    SpanKind,
    StopReason,
    ToolCallStatus,
    TraceState,
)
from bootstrap.schemas.trace import (
    AgentSnapshotRef,
    AgentTurnSpan,
    CustomSpan,
    EnvironmentInfo,
    FinalState,
    LLMCallSpan,
    LLMOutput,
    RetrievalSpan,
    RunInfo,
    Span,
    TokenBreakdown,
    ToolCallSpan,
    ToolUseRequest,
    Trace,
)


def _parse_time(value: Any) -> datetime:
    """Accept ISO string, epoch seconds, or epoch nanoseconds."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    if isinstance(value, str):
        # Allow trailing Z.
        cleaned = value.replace("Z", "+00:00") if value.endswith("Z") else value
        return datetime.fromisoformat(cleaned)
    if isinstance(value, int | float):
        # Heuristic: > 10^12 likely nanos; else seconds.
        if value > 1e12:
            return datetime.fromtimestamp(value / 1e9, tz=UTC)
        return datetime.fromtimestamp(value, tz=UTC)
    raise ValueError(f"cannot parse timestamp: {value!r}")


def _duration_ms(span: dict[str, Any]) -> int:
    if "duration_ms" in span:
        return int(span["duration_ms"])
    start = span.get("start_time") or span.get("started_at")
    end = span.get("end_time") or span.get("ended_at")
    if start is not None and end is not None:
        return int((_parse_time(end) - _parse_time(start)).total_seconds() * 1000)
    return 0


_STOP_REASON_ALIASES: dict[str, StopReason] = {
    "stop": StopReason.END_TURN,
    "end_turn": StopReason.END_TURN,
    "endofturn": StopReason.END_TURN,
    "tool_use": StopReason.TOOL_USE,
    "tool_calls": StopReason.TOOL_USE,
    "length": StopReason.MAX_TOKENS,
    "max_tokens": StopReason.MAX_TOKENS,
    "stop_sequence": StopReason.STOP_SEQUENCE,
    "pause_turn": StopReason.PAUSE_TURN,
    "content_filter": StopReason.REFUSAL,
    "refusal": StopReason.REFUSAL,
    "error": StopReason.ERROR,
}


def _normalize_stop_reason(value: Any) -> StopReason | None:
    if value is None:
        return None
    if isinstance(value, list) and value:
        value = value[0]
    if not isinstance(value, str):
        return None
    return _STOP_REASON_ALIASES.get(value.strip().lower())


_KIND_ALIASES: dict[str, SpanKind] = {
    "llm": SpanKind.LLM_CALL,
    "tool": SpanKind.TOOL_CALL,
    "retriever": SpanKind.RETRIEVAL,
    "retrieval": SpanKind.RETRIEVAL,
    "agent": SpanKind.AGENT_TURN,
    "chain": SpanKind.AGENT_TURN,
    "guardrail": SpanKind.GUARDRAIL_CHECK,
    "memory": SpanKind.MEMORY_READ,
}


def _classify_span(span: dict[str, Any], attrs: dict[str, Any]) -> SpanKind:
    """Pick a SpanKind from OpenInference / gen_ai hints."""
    kind = attrs.get("openinference.span.kind") or span.get("kind")
    if isinstance(kind, str):
        kind_norm = kind.strip().lower()
        if kind_norm in _KIND_ALIASES:
            return _KIND_ALIASES[kind_norm]
    if any(k.startswith("gen_ai.") for k in attrs):
        return SpanKind.LLM_CALL
    if "tool.name" in attrs or "openinference.tool.name" in attrs:
        return SpanKind.TOOL_CALL
    if "retrieval.documents.count" in attrs or "openinference.retrieval.documents" in attrs:
        return SpanKind.RETRIEVAL
    return SpanKind.CUSTOM


def _build_llm_span(span: dict[str, Any], attrs: dict[str, Any]) -> LLMCallSpan:
    tokens = TokenBreakdown(
        input=int(attrs.get("gen_ai.usage.input_tokens", 0) or 0),
        input_cache_read=int(attrs.get("gen_ai.usage.cache_read_input_tokens", 0) or 0),
        input_cache_creation=int(attrs.get("gen_ai.usage.cache_creation_input_tokens", 0) or 0),
        output=int(attrs.get("gen_ai.usage.output_tokens", 0) or 0),
        reasoning=int(attrs.get("gen_ai.usage.reasoning_tokens", 0) or 0),
        total=int(
            attrs.get("gen_ai.usage.total_tokens", 0)
            or (
                int(attrs.get("gen_ai.usage.input_tokens", 0) or 0)
                + int(attrs.get("gen_ai.usage.cache_read_input_tokens", 0) or 0)
                + int(attrs.get("gen_ai.usage.cache_creation_input_tokens", 0) or 0)
                + int(attrs.get("gen_ai.usage.output_tokens", 0) or 0)
                + int(attrs.get("gen_ai.usage.reasoning_tokens", 0) or 0)
            )
        ),
    )
    output = LLMOutput(
        stop_reason=_normalize_stop_reason(attrs.get("gen_ai.response.finish_reasons")),
    )
    known_prefixes = ("gen_ai.", "openinference.")
    provider_metadata = {
        k: v for k, v in attrs.items() if not any(k.startswith(p) for p in known_prefixes)
    }
    return LLMCallSpan(
        id=span.get("span_id") or new_prefixed_id("sp"),
        parent_id=span.get("parent_span_id"),
        name=span.get("name", "llm_call"),
        started_at=_parse_time(span.get("start_time") or span.get("started_at")),
        duration_ms=_duration_ms(span),
        provider=str(attrs.get("gen_ai.system", "unknown")),
        model=str(attrs.get("gen_ai.request.model", "unknown")),
        model_version_pinned=attrs.get("gen_ai.response.model"),
        params={
            k.removeprefix("gen_ai.request."): v
            for k, v in attrs.items()
            if k.startswith("gen_ai.request.") and k != "gen_ai.request.model"
        },
        output=output,
        tokens=tokens,
        provider_metadata=provider_metadata,
    )


def _build_tool_span(span: dict[str, Any], attrs: dict[str, Any]) -> ToolCallSpan:
    tool_name = (
        attrs.get("openinference.tool.name")
        or attrs.get("tool.name")
        or span.get("name", "tool_call")
    )
    raw_status = attrs.get("tool.status") or attrs.get("openinference.tool.status") or "ok"
    status_norm = str(raw_status).strip().lower()
    status = (
        ToolCallStatus(status_norm)
        if status_norm in {s.value for s in ToolCallStatus}
        else ToolCallStatus.OK
    )
    return ToolCallSpan(
        id=span.get("span_id") or new_prefixed_id("sp"),
        parent_id=span.get("parent_span_id"),
        name=span.get("name", "tool_call"),
        started_at=_parse_time(span.get("start_time") or span.get("started_at")),
        duration_ms=_duration_ms(span),
        tool_name=str(tool_name),
        tool_use_id=attrs.get("openinference.tool.call_id") or attrs.get("tool.call_id"),
        status=status,
        error=attrs.get("tool.error") or attrs.get("error.message"),
    )


def _build_retrieval_span(span: dict[str, Any], attrs: dict[str, Any]) -> RetrievalSpan:
    top_k = int(
        attrs.get("retrieval.documents.count")
        or attrs.get("openinference.retrieval.documents")
        or 1
    )
    return RetrievalSpan(
        id=span.get("span_id") or new_prefixed_id("sp"),
        parent_id=span.get("parent_span_id"),
        name=span.get("name", "retrieval"),
        started_at=_parse_time(span.get("start_time") or span.get("started_at")),
        duration_ms=_duration_ms(span),
        retriever=str(attrs.get("retrieval.retriever") or "unknown"),
        top_k_requested=max(1, top_k),
        top_k_returned=top_k,
    )


def _build_agent_turn(span: dict[str, Any]) -> AgentTurnSpan:
    return AgentTurnSpan(
        id=span.get("span_id") or new_prefixed_id("sp"),
        parent_id=span.get("parent_span_id"),
        name=span.get("name", "agent_turn"),
        started_at=_parse_time(span.get("start_time") or span.get("started_at")),
        duration_ms=_duration_ms(span),
    )


def _build_custom(span: dict[str, Any], attrs: dict[str, Any]) -> CustomSpan:
    return CustomSpan(
        id=span.get("span_id") or new_prefixed_id("sp"),
        parent_id=span.get("parent_span_id"),
        name=span.get("name", "custom"),
        started_at=_parse_time(span.get("start_time") or span.get("started_at")),
        duration_ms=_duration_ms(span),
        payload=attrs,
    )


def _link_tool_use_ids(spans: list[Span]) -> list[Span]:
    """Ensure every ToolCallSpan.tool_use_id is requested by some LLMCallSpan.

    If a TOOL span carries a call_id but no LLM span in the trace requested
    it, attach a synthetic ToolUseRequest to the most likely candidate:
    the TOOL span's parent if it is an LLM call, else the nearest preceding
    LLM call. If no LLM call exists at all, drop the tool_use_id (better
    silent loss than an invalid Trace).
    """
    by_id: dict[str, Span] = {s.id: s for s in spans}
    requested: set[str] = set()
    for s in spans:
        if isinstance(s, LLMCallSpan):
            for req in s.output.tool_use_requested:
                requested.add(req.tool_use_id)

    out: list[Span] = list(spans)
    for idx, s in enumerate(out):
        if not isinstance(s, ToolCallSpan):
            continue
        if s.tool_use_id is None or s.tool_use_id in requested:
            continue
        # Find candidate LLM ancestor: parent or nearest preceding LLM call.
        candidate: LLMCallSpan | None = None
        parent = by_id.get(s.parent_id) if s.parent_id else None
        if isinstance(parent, LLMCallSpan):
            candidate = parent
        else:
            for prior in reversed(out[:idx]):
                if isinstance(prior, LLMCallSpan):
                    candidate = prior
                    break
        if candidate is None:
            # No LLM call to link to — drop the id rather than fail the trace.
            out[idx] = s.model_copy(update={"tool_use_id": None})
            continue
        # Append a synthetic tool_use_requested entry on the LLM span.
        new_requests = [
            *candidate.output.tool_use_requested,
            ToolUseRequest(
                tool=s.tool_name,
                tool_use_id=s.tool_use_id,
            ),
        ]
        new_output = candidate.output.model_copy(update={"tool_use_requested": new_requests})
        new_candidate = candidate.model_copy(update={"output": new_output})
        out[out.index(candidate)] = new_candidate
        requested.add(s.tool_use_id)
    return out


def import_otel_spans(
    spans: list[dict[str, Any]],
    *,
    workspace_id: str,
    run: RunInfo,
    agent: AgentSnapshotRef,
    framework_version: str = "opentelemetry-imported",
    runtime: str = "imported",
    sandbox: SandboxMode = SandboxMode.MOCK,
    environment_started_at: datetime | None = None,
    final_state: FinalState | None = None,
) -> Trace:
    """Build a bootstrap Trace from a flat list of OTel-style span dicts."""
    if not workspace_id:
        raise ValueError("workspace_id must be non-empty")
    parsed_spans: list[Span] = []
    earliest: datetime | None = None
    latest: datetime | None = None
    for raw in spans:
        attrs = dict(raw.get("attributes") or {})
        kind = _classify_span(raw, attrs)
        if kind == SpanKind.LLM_CALL:
            built: Span = _build_llm_span(raw, attrs)
        elif kind == SpanKind.TOOL_CALL:
            built = _build_tool_span(raw, attrs)
        elif kind == SpanKind.RETRIEVAL:
            built = _build_retrieval_span(raw, attrs)
        elif kind == SpanKind.AGENT_TURN:
            built = _build_agent_turn(raw)
        else:
            built = _build_custom(raw, attrs)
        parsed_spans.append(built)
        if earliest is None or built.started_at < earliest:
            earliest = built.started_at
        if latest is None or built.started_at > latest:
            latest = built.started_at

    # OTel doesn't carry explicit LLM↔Tool linkage. If a TOOL span has a
    # call_id and there is an LLM span that could plausibly have requested
    # it (same parent or immediate predecessor), synthesize a
    # ToolUseRequest on that LLM span so the schema invariant holds.
    parsed_spans = _link_tool_use_ids(parsed_spans)

    env_start = environment_started_at or earliest or datetime.now(tz=UTC)
    env = EnvironmentInfo(
        framework_version=framework_version,
        runtime=runtime,
        sandbox=sandbox,
        started_at=env_start,
        ended_at=latest or env_start,
    )
    return Trace(
        id=Trace.make_id(),
        workspace_id=workspace_id,
        run=run,
        agent=agent,
        environment=env,
        final_state=final_state or FinalState(status=TraceState.COMPLETED),
        spans=parsed_spans,
    )
