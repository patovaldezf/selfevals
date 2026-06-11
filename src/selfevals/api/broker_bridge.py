"""Adapter that lets the OTLP receiver feed the SSE broker.

Lives in `selfevals.api` so the `runner/` package stays unaware that
SSE / FastAPI exist. Only `selfevals serve` imports this; CLI-only
runs never load it.

Status (2026-06): this is the *OTLP wire* path — for agents that export
spans over the network to the embedded receiver. It is NOT what powers
live streaming today. Embedded runs (the F1 `experiments/run` flow) stream
in-process via `recorder_sink.BrokerSpanSink`, which taps the recorder's
canonical spans directly and publishes them in `SpanSummary` shape.

The OTLP path is currently incomplete end-to-end: `publish()` here forwards
the receiver's raw OTLP-decoded dict (`otlp_to_recorder.DecodedSpan.payload`
— `span_id`/`start_time`-nanos/`attributes`), NOT the `SpanSummary` shape
the FE renders. Before wiring an OTLP-exporting agent to live SSE, insert a
projection from the decoded OTLP span to the `SpanSummary` view (mirror
`trace.span_view.span_view`). Until then, `selfevals serve` deliberately
does not start the receiver or install this publisher.
"""

from __future__ import annotations

from typing import Any

from selfevals.api.broker import SpanBrokerProtocol
from selfevals.runner.otlp_receiver import SpanPublisher


class BrokerPublisher(SpanPublisher):
    """SpanPublisher impl that forwards to a SpanBroker (OTLP wire path)."""

    def __init__(self, broker: SpanBrokerProtocol) -> None:
        self._broker = broker

    def mark_active(self, workspace_id: str, run_id: str) -> None:
        self._broker.mark_run_active_threadsafe(workspace_id, run_id)

    def publish(self, workspace_id: str, run_id: str, span_payload: dict[str, Any]) -> None:
        self._broker.publish_threadsafe(workspace_id, run_id, span_payload)

    def close(self, workspace_id: str, run_id: str, final_state: str = "completed") -> None:
        self._broker.close_run_threadsafe(workspace_id, run_id, final_state)
