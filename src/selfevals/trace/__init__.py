"""Trace ingestion: payload routing, native SDK decorators, and OTel import.

The trace package writes Trace + Span entities by composing three parts:

- PayloadRouter — keeps small payloads inline, pushes large ones to the
  object store and substitutes a pointer + hash.
- TraceRecorder — context manager that captures spans during execution and
  flushes a Trace at the end.
- decorators — `@trace_agent_turn`, `@trace_tool`, `@trace_llm_call` —
  convenience wrappers around TraceRecorder.
- otel — adapter that turns OTel spans (gen_ai.*, openinference.*) into
  selfevals spans.
"""

from selfevals.trace.otel_importer import import_otel_spans
from selfevals.trace.payload_router import (
    DEFAULT_INLINE_THRESHOLD_BYTES,
    PayloadDecision,
    PayloadRouter,
    RoutedPayload,
)
from selfevals.trace.recorder import TraceRecorder

__all__ = [
    "DEFAULT_INLINE_THRESHOLD_BYTES",
    "PayloadDecision",
    "PayloadRouter",
    "RoutedPayload",
    "TraceRecorder",
    "import_otel_spans",
]
