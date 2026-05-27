"""Bridge OTLP protobuf payloads → selfevals Trace spans.

The OTel HTTP exporter ships `ExportTraceServiceRequest` protobuf bodies.
This module decodes one such body into the flat list-of-dicts shape that
`selfevals.trace.otel_importer` already consumes, then feeds those dicts
into a live `TraceRecorder` (or stashes them for later if no recorder is
currently bound).

The dict shape we emit per span:

    {
        "name": str,
        "span_id": str,             # hex
        "parent_span_id": str|None, # hex
        "kind": "LLM"|"TOOL"|...    # optional, from openinference.span.kind
        "start_time": int,          # nanos since epoch
        "end_time": int,
        "attributes": dict[str, Any],
    }

That matches the keys consumed by `otel_importer._build_*` helpers, so
we reuse the existing classification + mapping code rather than
re-inventing it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class DecodedSpan:
    """Spans in the dict-form `otel_importer` expects."""

    payload: dict[str, Any]


def decode_otlp_protobuf(body: bytes) -> list[DecodedSpan]:
    """Decode an OTLP/HTTP protobuf body into our internal dict spans.

    Returns an empty list (and logs) if the protobuf libs aren't available.
    """
    try:
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
            ExportTraceServiceRequest,
        )
    except ImportError:
        logger.warning(
            "opentelemetry-proto not installed; cannot decode OTLP payloads. "
            "Install with `pip install 'selfevals[telemetry]'`."
        )
        return []

    request = ExportTraceServiceRequest()
    request.ParseFromString(body)
    spans: list[DecodedSpan] = []
    for resource_spans in request.resource_spans:
        resource_attrs = _decode_kv(resource_spans.resource.attributes)
        for scope_spans in resource_spans.scope_spans:
            for span in scope_spans.spans:
                attrs = _decode_kv(span.attributes)
                # Resource attributes (like selfevals.iteration_id) get
                # merged into the per-span attribute dict so downstream
                # routing can pick them up without a separate channel.
                for k, v in resource_attrs.items():
                    attrs.setdefault(k, v)
                spans.append(
                    DecodedSpan(
                        payload={
                            "name": span.name,
                            "span_id": span.span_id.hex(),
                            "parent_span_id": (
                                span.parent_span_id.hex() if span.parent_span_id else None
                            ),
                            "trace_id": span.trace_id.hex(),
                            "start_time": int(span.start_time_unix_nano),
                            "end_time": int(span.end_time_unix_nano),
                            "duration_ms": max(
                                0,
                                int(
                                    (span.end_time_unix_nano - span.start_time_unix_nano)
                                    / 1_000_000
                                ),
                            ),
                            "attributes": attrs,
                        }
                    )
                )
    return spans


def _decode_kv(kvlist: Any) -> dict[str, Any]:
    """Turn OTel's KeyValue/AnyValue protobuf list into a plain dict."""
    out: dict[str, Any] = {}
    for kv in kvlist:
        out[kv.key] = _decode_any_value(kv.value)
    return out


def _decode_any_value(value: Any) -> Any:
    """Unwrap an OTel AnyValue protobuf into a native Python value."""
    # AnyValue uses a `WhichOneof("value")`-style discriminator.
    which = value.WhichOneof("value")
    if which is None:
        return None
    if which == "string_value":
        return value.string_value
    if which == "bool_value":
        return value.bool_value
    if which == "int_value":
        return value.int_value
    if which == "double_value":
        return value.double_value
    if which == "array_value":
        return [_decode_any_value(v) for v in value.array_value.values]
    if which == "kvlist_value":
        return _decode_kv(value.kvlist_value.values)
    if which == "bytes_value":
        return value.bytes_value
    return None


def feed_to_recorder(spans: list[DecodedSpan], recorder: Any) -> int:
    """Append OTLP-derived spans onto a live TraceRecorder.

    Returns the count of spans successfully appended. Uses the same
    classification + builders as `otel_importer.import_otel_spans`, but
    skips trace-level reconstruction because the recorder already owns
    the EnvironmentInfo / RunInfo / FinalState envelope.

    Spans are appended in arrival order. Token totals on the recorder
    are bumped to match `LLMCallSpan` contributions so trace metrics
    stay accurate.
    """
    if not spans:
        return 0
    from selfevals.schemas.enums import SpanKind
    from selfevals.schemas.trace import LLMCallSpan, Span
    from selfevals.trace.otel_importer import (
        _build_agent_turn,
        _build_custom,
        _build_llm_span,
        _build_retrieval_span,
        _build_tool_span,
        _classify_span,
    )

    appended = 0
    for dspan in spans:
        raw = dspan.payload
        attrs = dict(raw.get("attributes") or {})
        kind = _classify_span(raw, attrs)
        built: Span
        if kind == SpanKind.LLM_CALL:
            built = _build_llm_span(raw, attrs)
        elif kind == SpanKind.TOOL_CALL:
            built = _build_tool_span(raw, attrs)
        elif kind == SpanKind.RETRIEVAL:
            built = _build_retrieval_span(raw, attrs)
        elif kind == SpanKind.AGENT_TURN:
            built = _build_agent_turn(raw)
        else:
            built = _build_custom(raw, attrs)
        # Append directly to the recorder's internal span list. We do
        # this rather than going through the context-manager API because
        # OTLP spans are pre-baked — we already have their durations and
        # parent ids; running them through a `with` block would
        # double-count time and lose parent ids.
        recorder._spans.append(built)
        if isinstance(built, LLMCallSpan):
            recorder._llm_call_count += 1
            recorder._tokens_in += built.tokens.input + built.tokens.input_cache_read
            recorder._tokens_out += built.tokens.output
            recorder._retries += built.retries
        appended += 1
    return appended
