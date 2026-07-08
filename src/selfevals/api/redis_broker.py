"""Redis-backed broker for live span streaming.

This adapter keeps the same narrow contract as `SpanBroker`, but stores run
events in Redis Streams so multiple API processes can publish/subscribe through
one shared transport. The worker (which may be a *separate process* under
distributed sharding) publishes spans via XADD; the API's SSE handlers read them
via XREAD. `get_broker()` selects this implementation whenever
`SELFEVALS_REDIS_URL` is set, so the live stream crosses process boundaries.

Failure posture: **fail-open**, mirroring the rate limiter. A Redis blip must not
kill a run. The in-process `SpanBroker` can never fail (it drops silently when no
loop is bound); the Redis writes here can, so they are wrapped to log once and
drop rather than letting the exception propagate into the worker and abort the
eval. The live stream is a best-effort observability channel, not a correctness
gate.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

from selfevals.api.broker import _Closed

logger = logging.getLogger(__name__)

_ACTIVE_RUNS_KEY = "selfevals:active_runs"
_CLOSED_TTL_SECONDS = 24 * 60 * 60
_STREAM_MAXLEN = 1_000
_XREAD_BLOCK_MS = 15_000


def _redis_error_types() -> tuple[type[BaseException], ...]:
    """Redis/client errors the broker treats as a degraded transport.

    `redis` is an optional extra, so its exception classes can't be imported at
    module load time. Duplicated locally rather than imported from
    `runner/redis_throttle.py` to avoid an api→runner cross-package dependency.
    """
    error_types: list[type[BaseException]] = [ConnectionError, TimeoutError, OSError]
    try:
        from redis.exceptions import RedisError
    except ImportError:
        return tuple(error_types)
    error_types.append(RedisError)
    return tuple(error_types)


_ERROR_TYPES: tuple[type[BaseException], ...] = _redis_error_types()


class RedisSpanBroker:
    """Redis Streams fan-out from run workers to SSE subscribers."""

    def __init__(
        self,
        redis_url: str,
        *,
        sync: Any = None,
        async_factory: Any = None,
    ) -> None:
        """`sync`/`async_factory` are injection seams for tests (stand-in clients);
        production leaves them None and we build real `redis` clients from
        `redis_url`."""
        self._redis_url = redis_url
        self._loop: asyncio.AbstractEventLoop | None = None
        self._degraded_once = False
        if sync is not None or async_factory is not None:
            self._sync: Any = sync
            self._async_factory: Any = async_factory
            return
        try:
            import redis
            import redis.asyncio as async_redis
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "Redis broker requires the redis extra: pip install 'selfevals[redis]'"
            ) from exc
        self._sync = redis.Redis.from_url(redis_url, decode_responses=True)
        self._async_factory = async_redis.Redis.from_url

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """No-op: unlike the in-process broker, Redis needs no event loop.

        The in-process `SpanBroker` binds the API loop so the OTLP receiver
        thread can `call_soon_threadsafe` its publishes onto it. Redis writes go
        straight to the shared server (sync client) and reads happen on the SSE
        handler's own loop, so there is nothing to hop to. Kept to satisfy the
        `SpanBrokerProtocol` contract — `app.py` calls it unconditionally."""
        self._loop = loop

    def _on_redis_error(self, op: str, exc: BaseException) -> None:
        """Log the first Redis failure, then drop silently (fail-open).

        The operator only needs to learn once that this broker lost Redis; after
        that, spamming a WARNING per dropped span helps no one. Subsequent
        failures are swallowed so the run keeps going on a best-effort stream."""
        if not self._degraded_once:
            self._degraded_once = True
            logger.warning(
                "RedisSpanBroker degraded (op=%s): %s; live stream is best-effort",
                op,
                exc,
            )

    def active_runs(self) -> list[tuple[str, str]]:
        try:
            values = self._sync.smembers(_ACTIVE_RUNS_KEY)
        except _ERROR_TYPES as exc:
            # An infra probe must never fail the request: degrade to "no active
            # runs known" rather than 500 the endpoint.
            self._on_redis_error("active_runs", exc)
            return []
        runs: list[tuple[str, str]] = []
        for value in values:
            workspace_id, sep, run_id = str(value).partition("\t")
            if sep:
                runs.append((workspace_id, run_id))
        return runs

    def mark_run_active_threadsafe(self, workspace_id: str, run_id: str) -> None:
        try:
            self._sync.sadd(_ACTIVE_RUNS_KEY, _run_member(workspace_id, run_id))
        except _ERROR_TYPES as exc:
            self._on_redis_error("mark_run_active", exc)

    async def subscribe(
        self, workspace_id: str, run_id: str
    ) -> AsyncIterator[dict[str, Any] | _Closed]:
        closed = self._sync.get(_closed_key(workspace_id, run_id))
        if closed is not None:
            yield _Closed(final_state=str(closed))
            return

        client = self._async_factory(self._redis_url, decode_responses=True)
        stream_key = _stream_key(workspace_id, run_id)
        # Start at "0" (not "$") so a late subscriber replays the spans already in
        # the stream — the in-flight ones not yet persisted (`persist_traces` may
        # only write on completion) would otherwise be lost between the snapshot
        # and going live. The FE deduplicates by `span.id`, so the overlap with
        # the SSE handler's persisted snapshot is harmless. `maxlen` bounds replay.
        last_id = "0"
        try:
            while True:
                rows = await client.xread(
                    {stream_key: last_id},
                    count=100,
                    block=_XREAD_BLOCK_MS,
                )
                if not rows:
                    continue
                for _stream_name, messages in rows:
                    for message_id, fields in messages:
                        last_id = str(message_id)
                        final_state = fields.get("closed")
                        if final_state is not None:
                            yield _Closed(final_state=str(final_state))
                            return
                        payload = fields.get("payload")
                        if payload is not None:
                            yield json.loads(str(payload))
        except _ERROR_TYPES as exc:
            # Redis died mid-stream: end the subscription cleanly so the SSE
            # handler emits `complete` and the FE drops out of live mode, rather
            # than the generator raising and the connection hanging.
            self._on_redis_error("subscribe", exc)
            yield _Closed(final_state="disconnected")
        finally:
            close = getattr(client, "aclose", None)
            if callable(close):
                with suppress(Exception):
                    await close()

    def publish_threadsafe(
        self, workspace_id: str, run_id: str, span_payload: dict[str, Any]
    ) -> None:
        try:
            self._sync.sadd(_ACTIVE_RUNS_KEY, _run_member(workspace_id, run_id))
            self._sync.xadd(
                _stream_key(workspace_id, run_id),
                {"payload": json.dumps(span_payload, separators=(",", ":"))},
                maxlen=_STREAM_MAXLEN,
                approximate=True,
            )
        except _ERROR_TYPES as exc:
            self._on_redis_error("publish", exc)

    def close_run_threadsafe(
        self, workspace_id: str, run_id: str, final_state: str = "completed"
    ) -> None:
        stream_key = _stream_key(workspace_id, run_id)
        try:
            self._sync.xadd(
                stream_key,
                {"closed": final_state},
                maxlen=_STREAM_MAXLEN,
                approximate=True,
            )
            self._sync.setex(_closed_key(workspace_id, run_id), _CLOSED_TTL_SECONDS, final_state)
            self._sync.srem(_ACTIVE_RUNS_KEY, _run_member(workspace_id, run_id))
            with suppress(Exception):
                self._sync.expire(stream_key, _CLOSED_TTL_SECONDS)
        except _ERROR_TYPES as exc:
            self._on_redis_error("close_run", exc)


def _run_member(workspace_id: str, run_id: str) -> str:
    return f"{workspace_id}\t{run_id}"


def _stream_key(workspace_id: str, run_id: str) -> str:
    return f"selfevals:spans:{workspace_id}:{run_id}"


def _closed_key(workspace_id: str, run_id: str) -> str:
    return f"selfevals:closed:{workspace_id}:{run_id}"
