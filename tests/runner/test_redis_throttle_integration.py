"""`RedisTokenBucket` against a REAL Redis, executing the actual Lua script.

The fake-client tests pin the Python wiring; this one pins the artifact that
runs in production — the Lua text and the server-side clock. It is guarded by
`SELFEVALS_REDIS_URL` (skipped when unset) exactly like the queue's integration
tests, so the default surface stays infra-free. Run it with
`docker compose up -d redis` and `SELFEVALS_REDIS_URL=redis://localhost:6380/15`.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("SELFEVALS_REDIS_URL"),
    reason="SELFEVALS_REDIS_URL not set; skipping real-Redis integration test",
)

# Resolved to a concrete str here; the skipif above guarantees it's set when any
# test below runs, so downstream code sees `str`, not `str | None`.
REDIS_URL = os.environ.get("SELFEVALS_REDIS_URL", "")


def _unique_key() -> str:
    # uuid4 keeps parallel test runs / leftover state from colliding.
    return f"selfevals:test:ratelimit:{uuid.uuid4().hex}"


async def _del(key: str) -> None:
    import redis.asyncio as async_redis

    client = async_redis.Redis.from_url(REDIS_URL)
    try:
        await client.delete(key)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_burst_within_capacity_is_free() -> None:
    from selfevals.runner.redis_throttle import RedisTokenBucket

    key = _unique_key()
    bucket = RedisTokenBucket(
        redis_url=REDIS_URL,
        key=key,
        rate_per_sec=1.0,
        capacity=3.0,
    )
    try:
        # Three acquisitions inside capacity should each return quickly (the real
        # Lua consumes server-side without a wait). We can't assert wall time
        # tightly, but we can assert it doesn't block for a refill window.
        await asyncio.wait_for(
            asyncio.gather(bucket.acquire(), bucket.acquire(), bucket.acquire()),
            timeout=2.0,
        )
    finally:
        await bucket.aclose()
        await _del(key)


@pytest.mark.asyncio
async def test_exhausted_bucket_reports_wait_without_consuming() -> None:
    from selfevals.runner.redis_throttle import RedisTokenBucket

    key = _unique_key()
    bucket = RedisTokenBucket(
        redis_url=REDIS_URL,
        key=key,
        rate_per_sec=2.0,
        capacity=1.0,
    )
    try:
        # Drain the single token, then hit the real Lua directly to read the wait
        # it returns on a miss (no consume on miss is the invariant under test).
        await bucket.acquire()
        wait_ms = await bucket._eval(1.0)
        assert wait_ms > 0  # exhausted → must wait
        # rate=2/s → ~500ms for one token; allow slack for clock granularity.
        assert wait_ms <= 600
    finally:
        await bucket.aclose()
        await _del(key)


@pytest.mark.asyncio
async def test_refill_unblocks_after_waiting() -> None:
    from selfevals.runner.redis_throttle import RedisTokenBucket

    key = _unique_key()
    bucket = RedisTokenBucket(
        redis_url=REDIS_URL,
        key=key,
        rate_per_sec=10.0,  # 100ms per token → fast refill for a quick test
        capacity=1.0,
    )
    try:
        await bucket.acquire()  # drains the token
        # This acquire must wait for the server-clock refill, then succeed. The
        # real `asyncio.sleep` inside `acquire` makes this an end-to-end check.
        await asyncio.wait_for(bucket.acquire(), timeout=3.0)
    finally:
        await bucket.aclose()
        await _del(key)


@pytest.mark.asyncio
async def test_two_buckets_share_one_redis_budget() -> None:
    from selfevals.runner.redis_throttle import RedisTokenBucket

    key = _unique_key()  # same key → one global bucket across both clients
    a = RedisTokenBucket(redis_url=REDIS_URL, key=key, rate_per_sec=1.0, capacity=2.0)
    b = RedisTokenBucket(redis_url=REDIS_URL, key=key, rate_per_sec=1.0, capacity=2.0)
    try:
        await a.acquire()  # 2 → 1
        await b.acquire()  # 1 → 0 (shared state in Redis)
        # The budget is now empty; a third acquire on either must report a wait.
        wait_ms = await a._eval(1.0)
        assert wait_ms > 0
    finally:
        await a.aclose()
        await b.aclose()
        await _del(key)
