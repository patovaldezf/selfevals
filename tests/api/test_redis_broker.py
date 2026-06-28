"""`RedisSpanBroker` wiring — fake Redis clients, no network.

The fake sync/async clients stand in for redis-py so these run on the default
surface without the extra or a server. They pin the *wiring* — fail-open on the
three writes, the closed-key short-circuit, payload/closed parsing, the
`last_id="0"` replay start, mid-stream degradation — NOT real Redis semantics,
which the guarded integration test covers. The broker is built through its
`sync`/`async_factory` injection seams, so `__init__` never touches redis-py.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from selfevals.api.broker import _Closed
from selfevals.api.redis_broker import RedisSpanBroker


class _FakeSyncRedis:
    """In-memory sync client: only the ops the broker's writes/reads touch.

    Any op named in `fail_on` raises ConnectionError, to exercise fail-open.
    """

    def __init__(self, fail_on: set[str] | None = None) -> None:
        self.sets: dict[str, set[str]] = {}
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.kv: dict[str, str] = {}
        self._seq = 0
        self._fail_on = fail_on or set()

    def _maybe_fail(self, op: str) -> None:
        if op in self._fail_on:
            raise ConnectionError(f"redis down ({op})")

    def sadd(self, key: str, member: str) -> None:
        self._maybe_fail("sadd")
        self.sets.setdefault(key, set()).add(member)

    def srem(self, key: str, member: str) -> None:
        self._maybe_fail("srem")
        self.sets.get(key, set()).discard(member)

    def smembers(self, key: str) -> set[str]:
        self._maybe_fail("smembers")
        return set(self.sets.get(key, set()))

    def xadd(self, key: str, fields: dict[str, str], **_: Any) -> str:
        self._maybe_fail("xadd")
        self._seq += 1
        message_id = f"{self._seq}-0"
        self.streams.setdefault(key, []).append((message_id, fields))
        return message_id

    def setex(self, key: str, _ttl: int, value: str) -> None:
        self._maybe_fail("setex")
        self.kv[key] = value

    def get(self, key: str) -> str | None:
        self._maybe_fail("get")
        return self.kv.get(key)

    def expire(self, key: str, _ttl: int) -> None:
        self._maybe_fail("expire")


class _FakeAsyncRedis:
    """In-memory async client for `subscribe`'s xread loop.

    `xread` returns queued rows on the first call and `[]` afterwards (a timeout),
    so the generator advances deterministically instead of blocking 15s. Set
    `fail_after` to raise on the Nth xread to exercise mid-stream degradation.
    `xread_calls` records the `streams` arg of every call for assertions.
    """

    def __init__(
        self,
        rows: list[tuple[str, dict[str, str]]],
        *,
        stream_key: str,
        fail_after: int | None = None,
    ) -> None:
        self._rows = rows
        self._stream_key = stream_key
        self._fail_after = fail_after
        self._calls = 0
        self.xread_calls: list[dict[str, str]] = []
        self.closed = False

    async def xread(self, streams: dict[str, str], **_: Any) -> list[Any]:
        self._calls += 1
        self.xread_calls.append(dict(streams))
        if self._fail_after is not None and self._calls >= self._fail_after:
            raise ConnectionError("redis down (xread)")
        if self._calls == 1 and self._rows:
            return [(self._stream_key, list(self._rows))]
        return []

    async def aclose(self) -> None:
        self.closed = True


def _broker(
    sync: _FakeSyncRedis,
    *,
    async_client: _FakeAsyncRedis | None = None,
) -> RedisSpanBroker:
    """Build a broker around fakes via the injection seams (no real Redis)."""

    def _factory(_url: str, **_kw: Any) -> _FakeAsyncRedis:
        assert async_client is not None
        return async_client

    return RedisSpanBroker("redis://unused", sync=sync, async_factory=_factory)


def test_publish_writes_payload_and_marks_active() -> None:
    sync = _FakeSyncRedis()
    broker = _broker(sync)
    broker.publish_threadsafe("ws", "run", {"id": "s1", "name": "first"})
    assert "ws\trun" in sync.sets["selfevals:active_runs"]
    stream = sync.streams["selfevals:spans:ws:run"]
    assert len(stream) == 1
    assert json.loads(stream[0][1]["payload"]) == {"id": "s1", "name": "first"}


def test_publish_fails_open_when_xadd_raises() -> None:
    sync = _FakeSyncRedis(fail_on={"xadd"})
    broker = _broker(sync)
    broker.publish_threadsafe("ws", "run", {"id": "s1"})  # must not raise
    assert broker._degraded_once is True


def test_close_fails_open_when_setex_raises() -> None:
    sync = _FakeSyncRedis(fail_on={"setex"})
    broker = _broker(sync)
    broker.close_run_threadsafe("ws", "run", "completed")  # must not raise
    assert broker._degraded_once is True


def test_mark_fails_open_when_sadd_raises() -> None:
    sync = _FakeSyncRedis(fail_on={"sadd"})
    broker = _broker(sync)
    broker.mark_run_active_threadsafe("ws", "run")  # must not raise
    assert broker._degraded_once is True


def test_degraded_logs_exactly_once(caplog: pytest.LogCaptureFixture) -> None:
    sync = _FakeSyncRedis(fail_on={"xadd", "sadd"})
    broker = _broker(sync)
    with caplog.at_level(logging.WARNING, logger="selfevals.api.redis_broker"):
        broker.publish_threadsafe("ws", "run", {"id": "s1"})
        broker.publish_threadsafe("ws", "run", {"id": "s2"})
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1


@pytest.mark.asyncio
async def test_subscribe_to_closed_run_yields_close_immediately() -> None:
    sync = _FakeSyncRedis()
    sync.kv["selfevals:closed:ws:run"] = "completed"
    broker = _broker(sync, async_client=_FakeAsyncRedis([], stream_key="x"))
    events = [ev async for ev in broker.subscribe("ws", "run")]
    assert events == [_Closed(final_state="completed")]


@pytest.mark.asyncio
async def test_subscribe_parses_payload_then_close() -> None:
    stream_key = "selfevals:spans:ws:run"
    rows = [
        ("1-0", {"payload": json.dumps({"id": "s1", "name": "first"})}),
        ("2-0", {"closed": "completed"}),
    ]
    async_client = _FakeAsyncRedis(rows, stream_key=stream_key)
    broker = _broker(_FakeSyncRedis(), async_client=async_client)
    events = [ev async for ev in broker.subscribe("ws", "run")]
    assert events[0] == {"id": "s1", "name": "first"}
    assert events[1] == _Closed(final_state="completed")
    assert async_client.closed is True  # aclose() ran in finally


@pytest.mark.asyncio
async def test_subscribe_starts_replay_at_zero() -> None:
    stream_key = "selfevals:spans:ws:run"
    rows = [("1-0", {"closed": "completed"})]
    async_client = _FakeAsyncRedis(rows, stream_key=stream_key)
    broker = _broker(_FakeSyncRedis(), async_client=async_client)
    _ = [ev async for ev in broker.subscribe("ws", "run")]
    # First xread must replay from "0", not "$" — so late subscribers see history.
    assert async_client.xread_calls[0][stream_key] == "0"


@pytest.mark.asyncio
async def test_subscribe_degrades_to_disconnected_on_midstream_error() -> None:
    stream_key = "selfevals:spans:ws:run"
    rows = [("1-0", {"payload": json.dumps({"id": "s1"})})]
    # Fail on the 2nd xread (after delivering the first payload).
    async_client = _FakeAsyncRedis(rows, stream_key=stream_key, fail_after=2)
    broker = _broker(_FakeSyncRedis(), async_client=async_client)
    events = [ev async for ev in broker.subscribe("ws", "run")]
    assert events[0] == {"id": "s1"}
    assert events[-1] == _Closed(final_state="disconnected")
    assert broker._degraded_once is True
    assert async_client.closed is True


def test_active_runs_parses_members_and_skips_garbage() -> None:
    sync = _FakeSyncRedis()
    sync.sets["selfevals:active_runs"] = {"ws\trun", "garbage_no_tab"}
    broker = _broker(sync)
    assert broker.active_runs() == [("ws", "run")]


def test_active_runs_fails_open_to_empty() -> None:
    sync = _FakeSyncRedis(fail_on={"smembers"})
    broker = _broker(sync)
    assert broker.active_runs() == []
    assert broker._degraded_once is True


def test_bind_loop_is_noop() -> None:
    import asyncio

    sync = _FakeSyncRedis()
    broker = _broker(sync)
    loop = asyncio.new_event_loop()
    try:
        broker.bind_loop(loop)  # must not raise
    finally:
        loop.close()
