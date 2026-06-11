"""Redis-backed broker for live span streaming.

This adapter keeps the same narrow contract as `SpanBroker`, but stores run
events in Redis Streams so multiple API processes can publish/subscribe through
one shared transport.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

from selfevals.api.broker import _Closed

_ACTIVE_RUNS_KEY = "selfevals:active_runs"
_CLOSED_TTL_SECONDS = 24 * 60 * 60
_STREAM_MAXLEN = 1_000
_XREAD_BLOCK_MS = 15_000


class RedisSpanBroker:
    """Redis Streams fan-out from run workers to SSE subscribers."""

    def __init__(self, redis_url: str) -> None:
        try:
            import redis
            import redis.asyncio as async_redis
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "Redis broker requires the redis extra: pip install 'selfevals[redis]'"
            ) from exc
        self._redis_url = redis_url
        self._sync: Any = redis.Redis.from_url(redis_url, decode_responses=True)
        self._async_factory: Any = async_redis.Redis.from_url
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def active_runs(self) -> list[tuple[str, str]]:
        values = self._sync.smembers(_ACTIVE_RUNS_KEY)
        runs: list[tuple[str, str]] = []
        for value in values:
            workspace_id, sep, run_id = str(value).partition("\t")
            if sep:
                runs.append((workspace_id, run_id))
        return runs

    def mark_run_active_threadsafe(self, workspace_id: str, run_id: str) -> None:
        self._sync.sadd(_ACTIVE_RUNS_KEY, _run_member(workspace_id, run_id))

    async def subscribe(
        self, workspace_id: str, run_id: str
    ) -> AsyncIterator[dict[str, Any] | _Closed]:
        closed = self._sync.get(_closed_key(workspace_id, run_id))
        if closed is not None:
            yield _Closed(final_state=str(closed))
            return

        client = self._async_factory(self._redis_url, decode_responses=True)
        stream_key = _stream_key(workspace_id, run_id)
        last_id = "$"
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
        finally:
            close = getattr(client, "aclose", None)
            if callable(close):
                await close()

    def publish_threadsafe(
        self, workspace_id: str, run_id: str, span_payload: dict[str, Any]
    ) -> None:
        self._sync.sadd(_ACTIVE_RUNS_KEY, _run_member(workspace_id, run_id))
        self._sync.xadd(
            _stream_key(workspace_id, run_id),
            {"payload": json.dumps(span_payload, separators=(",", ":"))},
            maxlen=_STREAM_MAXLEN,
            approximate=True,
        )

    def close_run_threadsafe(
        self, workspace_id: str, run_id: str, final_state: str = "completed"
    ) -> None:
        stream_key = _stream_key(workspace_id, run_id)
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


def _run_member(workspace_id: str, run_id: str) -> str:
    return f"{workspace_id}\t{run_id}"


def _stream_key(workspace_id: str, run_id: str) -> str:
    return f"selfevals:spans:{workspace_id}:{run_id}"


def _closed_key(workspace_id: str, run_id: str) -> str:
    return f"selfevals:closed:{workspace_id}:{run_id}"
