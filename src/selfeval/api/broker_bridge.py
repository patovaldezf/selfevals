"""Adapter that lets the OTLP receiver feed the SSE broker.

Lives in `selfeval.api` so the `runner/` package stays unaware that
SSE / FastAPI exist. Only `selfeval serve` imports this; CLI-only
runs never load it.
"""

from __future__ import annotations

from typing import Any

from selfeval.api.broker import SpanBroker
from selfeval.runner.otlp_receiver import SpanPublisher


class BrokerPublisher(SpanPublisher):
    """SpanPublisher impl that forwards to a SpanBroker."""

    def __init__(self, broker: SpanBroker) -> None:
        self._broker = broker

    def mark_active(self, workspace_id: str, run_id: str) -> None:
        self._broker.mark_run_active_threadsafe(workspace_id, run_id)

    def publish(self, workspace_id: str, run_id: str, span_payload: dict[str, Any]) -> None:
        self._broker.publish_threadsafe(workspace_id, run_id, span_payload)

    def close(self, workspace_id: str, run_id: str, final_state: str = "completed") -> None:
        self._broker.close_run_threadsafe(workspace_id, run_id, final_state)
