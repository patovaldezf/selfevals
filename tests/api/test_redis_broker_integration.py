"""`RedisSpanBroker` against a REAL Redis — the multi-process SSE path.

The fake-client tests pin the wiring; this one proves the actual transport:
spans published by one broker instance (the worker) reach a `subscribe` on a
*separate* instance (the API), through real Redis streams. Guarded by
`SELFEVALS_REDIS_URL` (skipped when unset), like the queue and rate-limiter
integration tests. Run with `docker compose up -d redis` and
`SELFEVALS_REDIS_URL=redis://localhost:6380/15`.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncGenerator
from typing import Any, cast

import pytest

from selfevals.api.broker import _Closed
from selfevals.api.redis_broker import RedisSpanBroker

pytestmark = pytest.mark.skipif(
    not os.environ.get("SELFEVALS_REDIS_URL"),
    reason="SELFEVALS_REDIS_URL not set; skipping real-Redis integration test",
)

REDIS_URL = os.environ.get("SELFEVALS_REDIS_URL", "")

WS = "ws_test"


def _run_id() -> str:
    return f"run_{uuid.uuid4().hex}"


def _cleanup(run_id: str) -> None:
    import redis

    client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        client.delete(f"selfevals:spans:{WS}:{run_id}")
        client.delete(f"selfevals:closed:{WS}:{run_id}")
        client.srem("selfevals:active_runs", f"{WS}\t{run_id}")
    finally:
        client.close()


async def _drain(broker: RedisSpanBroker, run_id: str, *, timeout: float = 5.0) -> list[object]:
    """Collect events from subscribe until _Closed, with a hard timeout.

    Drives the async generator explicitly and `aclose()`s it in finally so its
    pending xread connection is torn down — otherwise `filterwarnings=error`
    trips on the leaked redis.asyncio connection / a pending task.
    """
    agen = cast(
        "AsyncGenerator[dict[str, Any] | _Closed, None]", broker.subscribe(WS, run_id)
    )

    async def _collect() -> list[object]:
        out: list[object] = []
        async for ev in agen:
            out.append(ev)
            if isinstance(ev, _Closed):
                break
        return out

    try:
        return await asyncio.wait_for(_collect(), timeout=timeout)
    finally:
        await agen.aclose()


@pytest.mark.asyncio
async def test_round_trip_cross_instance() -> None:
    run_id = _run_id()
    worker = RedisSpanBroker(REDIS_URL)  # the publishing process
    api = RedisSpanBroker(REDIS_URL)  # the subscribing process
    try:
        # Publish while the run is still open, then close to terminate the drain.
        # (Closing first would set closed_key, and subscribe short-circuits to
        # _Closed before reading the stream — that path is its own test below.)
        worker.publish_threadsafe(WS, run_id, {"id": "s1", "name": "first"})
        drain = asyncio.create_task(_drain(api, run_id))
        await asyncio.sleep(0.2)  # let the subscriber replay the first span
        worker.close_run_threadsafe(WS, run_id, "completed")
        events = await drain
        assert {"id": "s1", "name": "first"} in events
        assert events[-1] == _Closed(final_state="completed")
    finally:
        _cleanup(run_id)


@pytest.mark.asyncio
async def test_late_subscriber_replays_history() -> None:
    run_id = _run_id()
    worker = RedisSpanBroker(REDIS_URL)
    api = RedisSpanBroker(REDIS_URL)
    try:
        # Publish 3 spans BEFORE anyone subscribes, run still open — last_id="0"
        # must replay them to the late subscriber.
        for i in range(3):
            worker.publish_threadsafe(WS, run_id, {"id": f"s{i}", "n": i})
        drain = asyncio.create_task(_drain(api, run_id))
        await asyncio.sleep(0.2)  # let the replay land
        worker.close_run_threadsafe(WS, run_id, "completed")
        events = await drain
        ids = [e["id"] for e in events if isinstance(e, dict)]
        assert ids == ["s0", "s1", "s2"]
    finally:
        _cleanup(run_id)


@pytest.mark.asyncio
async def test_close_delivers_final_state() -> None:
    run_id = _run_id()
    broker = RedisSpanBroker(REDIS_URL)
    try:
        broker.publish_threadsafe(WS, run_id, {"id": "s1"})
        broker.close_run_threadsafe(WS, run_id, "failed")
        events = await _drain(broker, run_id)
        assert events[-1] == _Closed(final_state="failed")
    finally:
        _cleanup(run_id)


@pytest.mark.asyncio
async def test_subscribe_to_already_closed_run() -> None:
    run_id = _run_id()
    broker = RedisSpanBroker(REDIS_URL)
    try:
        broker.close_run_threadsafe(WS, run_id, "completed")
        events = await _drain(broker, run_id)
        # closed_key short-circuit → immediate _Closed, no stream read.
        assert events == [_Closed(final_state="completed")]
    finally:
        _cleanup(run_id)


@pytest.mark.asyncio
async def test_active_runs_reflects_mark_and_close() -> None:
    run_id = _run_id()
    broker = RedisSpanBroker(REDIS_URL)
    try:
        broker.mark_run_active_threadsafe(WS, run_id)
        assert (WS, run_id) in broker.active_runs()
        broker.close_run_threadsafe(WS, run_id, "completed")
        assert (WS, run_id) not in broker.active_runs()
    finally:
        _cleanup(run_id)
