"""Adapter: in-process `TraceRecorder` spans → SSE `SpanBroker`.

This is the live-streaming path for embedded runs. The executor's
`TraceRecorder` calls this sink as spans finish; we forward each one to the
`SpanBroker`, which fans it out to `/stream` subscribers.

It is the in-process twin of `broker_bridge.BrokerPublisher` (which serves
the OTLP receiver path, for agents that export spans over the wire). Both
land in the same broker; this one skips the OTLP round-trip because the
embedded executor already holds the canonical `Span` objects in memory.

Lives in `selfevals.api` so `runner/` and `trace/` stay unaware that SSE /
FastAPI exist — they only know the `SpanSink` protocol. Only `selfevals
serve` constructs this; CLI-only runs never import it.

Threading: the recorder runs on the F1 run thread, so every method here is
called off the event loop. `SpanBroker`'s `*_threadsafe` methods hop onto
the bound loop via `call_soon_threadsafe` and return immediately, so the
run thread never blocks on a subscriber.
"""

from __future__ import annotations

from typing import Any

from selfevals.api.broker import SpanBrokerProtocol


class BrokerSpanSink:
    """`SpanSink` implementation that forwards to a `SpanBroker`."""

    def __init__(self, broker: SpanBrokerProtocol) -> None:
        self._broker = broker

    def on_trace_started(self, workspace_id: str, run_id: str) -> None:
        self._broker.mark_run_active_threadsafe(workspace_id, run_id)

    def on_span_finished(
        self, workspace_id: str, run_id: str, span_view: dict[str, Any]
    ) -> None:
        self._broker.publish_threadsafe(workspace_id, run_id, span_view)

    def on_trace_finished(
        self, workspace_id: str, run_id: str, final_state: str
    ) -> None:
        self._broker.close_run_threadsafe(workspace_id, run_id, final_state)
