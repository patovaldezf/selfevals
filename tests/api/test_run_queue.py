"""`RedisRunJobQueue.active_consumers` — live-vs-ghost consumer counting.

A worker that crashes leaves its consumer registered in the group forever, so a
raw `XINFO GROUPS` count reports ghosts as live workers and the orphan-job
warning stays silent. `active_consumers` instead reads per-consumer `idle` and
counts only those seen recently. These tests pin that behaviour against a fake
Redis client (no network), plus the redaction helper.
"""

from __future__ import annotations

from typing import Any

from selfevals.api.run_queue import (
    LIVE_CONSUMER_MAX_IDLE_MS,
    RUN_JOBS_GROUP,
    RUN_JOBS_STREAM,
    RedisRunJobQueue,
)


class _FakeRedisClient:
    """Minimal stand-in: only what active_consumers touches."""

    def __init__(self, consumers: list[dict[str, Any]] | Exception) -> None:
        self._consumers = consumers

    def xinfo_consumers(self, stream: str, group: str) -> list[dict[str, Any]]:
        assert stream == RUN_JOBS_STREAM
        assert group == RUN_JOBS_GROUP
        if isinstance(self._consumers, Exception):
            raise self._consumers
        return self._consumers


def _queue_with(consumers: list[dict[str, Any]] | Exception) -> RedisRunJobQueue:
    """Build a queue around a fake client, bypassing the redis-touching __init__."""
    q = object.__new__(RedisRunJobQueue)
    q._client = _FakeRedisClient(consumers)  # type: ignore[attr-defined]
    q.stream = RUN_JOBS_STREAM
    q.group = RUN_JOBS_GROUP
    q.redis_label = "redis://localhost:6380/15"
    return q


def test_active_consumers_counts_only_live() -> None:
    q = _queue_with(
        [
            {"name": "live-1", "idle": 1_500},  # refreshed recently → live
            {"name": "ghost", "idle": 5_000_000},  # long dead → excluded
            {"name": "live-2", "idle": 0},
        ]
    )
    assert q.active_consumers() == 2


def test_active_consumers_zero_when_all_ghosts() -> None:
    # The exact failure mode from the incident: workers registered but dead.
    q = _queue_with(
        [
            {"name": "ghost-1", "idle": 9_999_999},
            {"name": "ghost-2", "idle": LIVE_CONSUMER_MAX_IDLE_MS + 1},
        ]
    )
    assert q.active_consumers() == 0


def test_active_consumers_counts_boundary_idle_as_live() -> None:
    q = _queue_with([{"name": "edge", "idle": LIVE_CONSUMER_MAX_IDLE_MS}])
    assert q.active_consumers() == 1


def test_active_consumers_missing_idle_is_live() -> None:
    # If a Redis build omits `idle`, fail open (count as live) rather than warn
    # spuriously about a worker that may well exist.
    q = _queue_with([{"name": "no-idle-field"}])
    assert q.active_consumers() == 1


def test_active_consumers_none_on_redis_error() -> None:
    # Group missing / Redis unreachable → None, so the launcher can tell
    # "couldn't check" from "zero live" and stays quiet.
    q = _queue_with(RuntimeError("NOGROUP no such group"))
    assert q.active_consumers() is None


def test_active_consumers_respects_custom_window() -> None:
    q = _queue_with([{"name": "c", "idle": 10_000}])
    assert q.active_consumers(max_idle_ms=5_000) == 0
    assert q.active_consumers(max_idle_ms=20_000) == 1
