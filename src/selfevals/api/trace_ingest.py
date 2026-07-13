"""Trace ingest — persist a Trace built outside selfevals' executor.

Most traces are produced in-process by the executor (embedded agents) or via the
OTLP receiver (cli/http agents). A voice agent is different: the Retell
Custom-LLM server runs as a standalone WebSocket process, reconstructs each call
as a Trace (voice_turn → stt → llm_call → tts) from Retell's events, and POSTs
the finished Trace here. This endpoint validates and persists it, and — when the
run is live — fans its spans to the broker so the trace viewer shows a
production voice call in real time, the same as an embedded run.

The Trace is the canonical contract, so ingest is just validate + persist + (best
effort) publish. Audio bytes referenced by `audio_pointer` must already be in the
object store (the caller uploads them via the payload path, or the pointers stay
unresolved until the call recording is fetched).
"""

from __future__ import annotations

from typing import Any

from selfevals.api.broker import SpanBrokerProtocol
from selfevals.schemas.trace import Trace
from selfevals.storage.interface import StorageInterface
from selfevals.trace.span_view import span_view


class TraceIngestError(ValueError):
    """The submitted trace is malformed or targets the wrong workspace."""


def ingest_trace(
    storage: StorageInterface,
    *,
    workspace_id: str,
    payload: dict[str, Any],
    broker: SpanBrokerProtocol | None = None,
) -> Trace:
    """Validate, persist, and (best-effort) live-publish a submitted Trace.

    Returns the persisted `Trace`. Raises `TraceIngestError` for a bad payload or
    a workspace mismatch (a caller can't smuggle a trace into another workspace).
    """
    # Mint an id/version when the caller didn't supply them — a voice server
    # posts the trace content, not selfevals' entity envelope. An explicit id is
    # honored (idempotent re-ingest of the same call).
    body = {**payload, "workspace_id": workspace_id}
    body.setdefault("id", Trace.make_id())
    body.setdefault("version", 1)
    try:
        trace = Trace.model_validate(body)
    except Exception as exc:  # audit:ignore[broad_exception_catches] — wrap Pydantic validation into a 422 domain error
        raise TraceIngestError(f"invalid trace: {exc}") from exc
    if trace.workspace_id != workspace_id:
        raise TraceIngestError("trace workspace_id does not match route workspace")

    with storage.open(workspace_id) as scope:
        scope.put_entity(trace)

    # Best-effort live fan-out: if a viewer is watching this run_id, push the
    # spans so a production voice call streams in real time. Never fail ingest
    # because the broker had no subscribers.
    if broker is not None:
        run_id = trace.run.run_id
        broker.mark_run_active_threadsafe(workspace_id, run_id)
        for span in trace.spans:
            broker.publish_threadsafe(workspace_id, run_id, span_view(span))
        broker.close_run_threadsafe(workspace_id, run_id, str(trace.final_state.status))
    return trace
