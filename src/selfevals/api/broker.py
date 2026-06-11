"""In-process pub/sub for live trace streaming.

The OTLP receiver thread calls `publish_threadsafe()` when a span lands.
FastAPI SSE handlers call `subscribe()` to get an async generator of
events for a given `(workspace_id, run_id)` pair.

Why this lives in `selfevals.api`: the broker is a *transport* concern,
not a capture concern. The receiver doesn't know what it's for; it
just calls a callback. Coupling the broker to the API package keeps
the capture pipeline import-graph clean (the SDK / OTLP receiver
don't need to know FastAPI exists).

Scaling note: this is a single-process in-memory broker. The contract
(`publish` + `subscribe`) is intentionally narrow so a Redis-backed
implementation can drop in later without touching callers.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)


# Sentinel objects on the queue — clearer than magic dicts.
@dataclass(frozen=True)
class _Closed:
    final_state: str = "completed"


class SpanBrokerProtocol(Protocol):
    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None: ...

    def active_runs(self) -> list[tuple[str, str]]: ...

    def mark_run_active_threadsafe(self, workspace_id: str, run_id: str) -> None: ...

    def subscribe(
        self, workspace_id: str, run_id: str
    ) -> AsyncIterator[dict[str, Any] | _Closed]: ...

    def publish_threadsafe(
        self, workspace_id: str, run_id: str, span_payload: dict[str, Any]
    ) -> None: ...

    def close_run_threadsafe(
        self, workspace_id: str, run_id: str, final_state: str = "completed"
    ) -> None: ...


_QUEUE_MAXSIZE = 256
"""Per-subscriber queue depth. If we exceed it, the slowest subscriber
gets disconnected — it's the wrong behaviour to backpressure the
receiver thread for one stuck browser tab."""


@dataclass
class _Subscriber:
    queue: asyncio.Queue[dict[str, Any] | _Closed]
    workspace_id: str
    run_id: str
    closed: bool = False


@dataclass
class _Channel:
    """All subscribers for one (workspace_id, run_id) pair."""

    subscribers: list[_Subscriber] = field(default_factory=list)
    closed: bool = False
    final_state: str | None = None


class SpanBroker:
    """In-proc fan-out from the OTLP receiver to SSE subscribers."""

    def __init__(self) -> None:
        self._channels: dict[tuple[str, str], _Channel] = {}
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Capture the FastAPI event loop so the receiver thread can
        schedule publishes onto it. Call once at app startup."""
        self._loop = loop

    def active_runs(self) -> list[tuple[str, str]]:
        """Snapshot of (workspace_id, run_id) channels that are open.

        Used by `GET /api/runs/active` so the web shell can show a
        live pill for in-flight runs. Includes runs whose channel was
        opened by `mark_run_active` even before any spans arrived."""
        return [(ws, run) for (ws, run), ch in self._channels.items() if not ch.closed]

    def mark_run_active_threadsafe(self, workspace_id: str, run_id: str) -> None:
        """Open the channel for a run before any spans flow. Lets the
        web's "active runs" pill light up the moment a run starts."""
        loop = self._loop
        if loop is None:
            return
        with suppress(RuntimeError):
            loop.call_soon_threadsafe(self._mark_active_sync, workspace_id, run_id)

    def _mark_active_sync(self, workspace_id: str, run_id: str) -> None:
        key = (workspace_id, run_id)
        self._channels.setdefault(key, _Channel())

    async def subscribe(
        self, workspace_id: str, run_id: str
    ) -> AsyncIterator[dict[str, Any] | _Closed]:
        """Async-iterate events for one run. Caller is responsible for
        cancelling the iteration when the client disconnects.

        Note: this does NOT replay history. The SSE handler emits a
        snapshot of the current Trace state *before* calling
        subscribe(), so the subscriber only needs new spans from here.
        """
        key = (workspace_id, run_id)
        sub = _Subscriber(
            queue=asyncio.Queue(maxsize=_QUEUE_MAXSIZE),
            workspace_id=workspace_id,
            run_id=run_id,
        )
        async with self._lock:
            channel = self._channels.setdefault(key, _Channel())
            channel.subscribers.append(sub)
            # If the channel is already closed, emit the close event and
            # return without ever blocking.
            already_closed = channel.closed
            final_state = channel.final_state
        if already_closed:
            yield _Closed(final_state=final_state or "completed")
            return
        try:
            while True:
                event = await sub.queue.get()
                if isinstance(event, _Closed):
                    yield event
                    return
                yield event
        finally:
            sub.closed = True
            async with self._lock:
                ch = self._channels.get(key)
                if ch is not None:
                    ch.subscribers = [s for s in ch.subscribers if not s.closed]
                    if not ch.subscribers and ch.closed:
                        self._channels.pop(key, None)

    def publish_threadsafe(
        self, workspace_id: str, run_id: str, span_payload: dict[str, Any]
    ) -> None:
        """Called from the OTLP receiver's background thread.

        Hops onto the FastAPI event loop via call_soon_threadsafe.
        If no loop is bound, drops silently — the broker is best-effort,
        not the source of truth (SQLite is)."""
        loop = self._loop
        if loop is None:
            return
        # Loop may be closed during process shutdown — best-effort.
        with suppress(RuntimeError):
            loop.call_soon_threadsafe(self._publish_sync, workspace_id, run_id, span_payload)

    def close_run_threadsafe(
        self, workspace_id: str, run_id: str, final_state: str = "completed"
    ) -> None:
        loop = self._loop
        if loop is None:
            return
        with suppress(RuntimeError):
            loop.call_soon_threadsafe(self._close_sync, workspace_id, run_id, final_state)

    def _publish_sync(self, workspace_id: str, run_id: str, span_payload: dict[str, Any]) -> None:
        key = (workspace_id, run_id)
        channel = self._channels.get(key)
        if channel is None or channel.closed:
            # No live subscribers and channel hasn't been opened — drop.
            # A late subscriber will start from the SQLite snapshot.
            return
        for sub in list(channel.subscribers):
            try:
                sub.queue.put_nowait(span_payload)
            except asyncio.QueueFull:
                logger.warning(
                    "SpanBroker: dropping slow subscriber ws=%s run=%s",
                    workspace_id,
                    run_id,
                )
                sub.closed = True
                with suppress(asyncio.QueueFull):
                    sub.queue.put_nowait(_Closed(final_state="disconnected"))

    def _close_sync(self, workspace_id: str, run_id: str, final_state: str) -> None:
        key = (workspace_id, run_id)
        channel = self._channels.get(key)
        if channel is None:
            # Subscribers may attach later; record the closed state so
            # subscribe() can emit _Closed immediately.
            self._channels[key] = _Channel(closed=True, final_state=final_state)
            return
        channel.closed = True
        channel.final_state = final_state
        close_event = _Closed(final_state=final_state)
        for sub in channel.subscribers:
            with suppress(asyncio.QueueFull):
                sub.queue.put_nowait(close_event)


REDIS_URL_ENV = "SELFEVALS_REDIS_URL"

# Module-level singleton bound at build_app() time.
_broker: SpanBrokerProtocol | None = None


def get_broker() -> SpanBrokerProtocol:
    """Return the process-wide broker, lazily constructed."""
    global _broker
    broker = _broker
    if broker is None:
        redis_url = os.environ.get(REDIS_URL_ENV)
        if redis_url:
            from selfevals.api.redis_broker import RedisSpanBroker

            broker = RedisSpanBroker(redis_url)
        else:
            broker = SpanBroker()
        _broker = broker
    return broker


def reset_for_tests() -> None:
    """Drop the singleton — used by test fixtures to keep state clean."""
    global _broker
    _broker = None
