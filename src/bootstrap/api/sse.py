"""SSE stream of trace spans, with a heartbeat and a snapshot prelude.

Wire format (one frame is `<lines>\\n\\n`):

    event: snapshot
    data: {... full Trace JSON ...}

    event: span
    data: {... one SpanSummary ...}

    event: ping
    data: 1

    event: complete
    data: {"final_state": "completed"}

The client subscribes via `new EventSource(url)`. Heartbeat every 15s
keeps proxies from idle-closing the connection.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

from fastapi.responses import StreamingResponse

from bootstrap.api.broker import SpanBroker, _Closed
from bootstrap.api.queries import load_trace
from bootstrap.storage.sqlite import SQLiteStorage

logger = logging.getLogger(__name__)

_HEARTBEAT_SECONDS = 15.0
_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _encode(event: str, data: Any) -> bytes:
    payload = data if isinstance(data, str) else json.dumps(data, default=str)
    return f"event: {event}\ndata: {payload}\n\n".encode()


async def stream_trace(
    *,
    workspace_id: str,
    run_id: str,
    broker: SpanBroker,
    storage_factory: Callable[[], SQLiteStorage],
) -> StreamingResponse:
    """Build a StreamingResponse that emits snapshot + live spans."""

    async def gen() -> AsyncIterator[bytes]:
        # 1. Initial snapshot from SQLite (may be None if the run hasn't
        #    persisted yet; that's fine — the client gets an empty
        #    snapshot and waits for live spans).
        storage = storage_factory()
        try:
            snapshot = load_trace(storage, workspace_id=workspace_id, trace_id=run_id)
        finally:
            storage.close()
        if snapshot is not None:
            yield _encode("snapshot", snapshot.model_dump(mode="json"))
        else:
            yield _encode("snapshot", {"run_id": run_id, "spans": []})

        # 2. Live subscription.
        sub = broker.subscribe(workspace_id, run_id)
        agen = sub.__aiter__()

        async def _next() -> dict[str, Any] | _Closed:
            return await agen.__anext__()

        heartbeat_task: asyncio.Task[None] | None = None
        next_event_task: asyncio.Task[dict[str, Any] | _Closed] | None = None
        try:
            while True:
                if next_event_task is None:
                    next_event_task = asyncio.create_task(_next())
                if heartbeat_task is None:
                    heartbeat_task = asyncio.create_task(asyncio.sleep(_HEARTBEAT_SECONDS))
                done, _pending = await asyncio.wait(
                    {next_event_task, heartbeat_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if heartbeat_task in done:
                    heartbeat_task = None
                    yield _encode("ping", "1")
                if next_event_task in done:
                    try:
                        event = next_event_task.result()
                    except StopAsyncIteration:
                        return
                    next_event_task = None
                    if isinstance(event, _Closed):
                        yield _encode("complete", {"final_state": event.final_state})
                        return
                    yield _encode("span", event)
        finally:
            for task in (heartbeat_task, next_event_task):
                if task is not None and not task.done():
                    task.cancel()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers=_HEADERS,
    )
