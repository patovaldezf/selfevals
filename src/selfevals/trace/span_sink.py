"""Live span sink — the seam between span capture and live consumers.

A `TraceRecorder` calls a `SpanSink` as spans open, close, and as the
trace finishes. The default `NoOpSpanSink` does nothing, so CLI-only runs
pay zero for a feature they don't use. `selfevals serve` injects a sink
backed by the SSE `SpanBroker` (`api.recorder_sink.BrokerSpanSink`), which
fans each span out to live `/stream` subscribers.

Why a sink (and not OTLP) for the in-process path: the embedded executor
already builds the canonical `Span` objects in memory via the recorder.
Round-tripping them through an OTLP exporter → localhost receiver →
decoder only to reconstruct the same spans would add a network hop and a
lossy protobuf translation for no gain. The sink taps the spans at the
source, in the exact `SpanSummary` view shape the REST snapshot uses.

Contract — the recorder runs on a background worker thread (the F1 run
thread), so a sink implementation MUST be non-blocking and thread-safe.
`BrokerSpanSink` satisfies this by hopping onto the FastAPI event loop via
`call_soon_threadsafe` and returning immediately. The recorder swallows
sink exceptions: a broken live stream must never fail a run (SQLite is the
source of truth; the stream is best-effort).
"""

from __future__ import annotations

from typing import Any, Protocol


class SpanSink(Protocol):
    """Receives live span events from a `TraceRecorder`.

    `workspace_id` / `run_id` identify the channel a consumer subscribes
    to. `span_view` is the JSON-safe `SpanSummary`-shaped dict produced by
    `trace.span_view.span_view`.
    """

    def on_trace_started(self, workspace_id: str, run_id: str) -> None:
        """Called once when the recorder is entered, before any span.

        Lets a consumer open the channel (light the "live" pill) the moment
        a run starts, even before the first span finishes.
        """
        ...

    def on_span_finished(
        self, workspace_id: str, run_id: str, span_view: dict[str, Any]
    ) -> None:
        """Called once per span, when the span context manager exits.

        Spans finish in completion order (innermost first), matching how
        the FE appends them; it sorts the tree by `started_at` regardless.
        """
        ...

    def on_trace_finished(
        self, workspace_id: str, run_id: str, final_state: str
    ) -> None:
        """Called once when the recorder exits, with the trace's terminal state."""
        ...


class NoOpSpanSink:
    """Default sink: drops every event. Used by CLI-only runs."""

    def on_trace_started(self, workspace_id: str, run_id: str) -> None:
        return None

    def on_span_finished(
        self, workspace_id: str, run_id: str, span_view: dict[str, Any]
    ) -> None:
        return None

    def on_trace_finished(
        self, workspace_id: str, run_id: str, final_state: str
    ) -> None:
        return None


NO_OP_SINK: SpanSink = NoOpSpanSink()
"""Shared singleton so callers needn't allocate a no-op per recorder."""
