"""`RedisTokenBucket` wiring — fake async client, no Redis, no network.

The fake reimplements the token-bucket arithmetic in Python against an
injectable clock, mirroring the Lua's semantics (consume on hit, wait-without-
consume on miss, lazy refill, capacity clamp). These tests pin the *wiring*
(acquire loop, fail-open, EVALSHA→NOSCRIPT fallback, the `RateLimitedAdapter`
drop-in) — NOT the literal Lua text, which the guarded integration test
exercises against real Redis. Construction goes through `object.__new__` to
bypass the `import redis.asyncio` in `__init__`, exactly as
`tests/api/test_run_queue.py` does for the queue.
"""

from __future__ import annotations

import math

import pytest

from selfevals.runner.adapters import AdapterRequest, AdapterResponse, AgentAdapter
from selfevals.runner.redis_throttle import RedisTokenBucket
from selfevals.runner.throttle import RateLimitedAdapter

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _req() -> AdapterRequest:
    return AdapterRequest(workspace_id=WS, case_id="ec_x", input={})


class _CountingAdapter(AgentAdapter):
    def __init__(self) -> None:
        self.calls = 0
        self.agent = None

    async def invoke(self, request: AdapterRequest) -> AdapterResponse:
        self.calls += 1
        return AdapterResponse(content="ok")


class _ClosableBucket:
    def __init__(self) -> None:
        self.closed: list[bool] = []

    async def acquire(self, tokens: float = 1.0) -> None:
        return None

    async def aclose(self) -> None:
        self.closed.append(True)


class _FakeClock:
    """Manual clock in ms; `sleep` (seconds) advances it deterministically."""

    def __init__(self) -> None:
        self.now_ms = 0.0
        self.slept: list[float] = []

    async def sleep(self, delay_seconds: float) -> None:
        self.slept.append(delay_seconds)
        self.now_ms += delay_seconds * 1000.0


class _FakeAsyncRedis:
    """In-memory stand-in implementing only eval/evalsha/script_load/aclose.

    Reimplements the bucket arithmetic against a shared `_FakeClock`, so a hit
    consumes and a miss returns ms-to-wait without consuming — the same contract
    the Lua honors. `raise_on_eval` lets a test simulate Redis being down.
    `noscript_once` makes the first `evalsha` raise NOSCRIPT to drive the
    fallback path.
    """

    def __init__(self, clock: _FakeClock) -> None:
        self._clock = clock
        # key -> (tokens, ts_ms)
        self.store: dict[str, tuple[float, float]] = {}
        self.raise_on_eval: Exception | None = None
        self.noscript_once = False
        self.eval_calls = 0
        self.evalsha_calls = 0
        self.script_load_calls = 0

    def _run(self, key: str, rate: float, capacity: float, requested: float) -> int:
        if self.raise_on_eval is not None:
            raise self.raise_on_eval
        now = self._clock.now_ms
        tokens, last_ms = self.store.get(key, (capacity, now))
        elapsed = (now - last_ms) / 1000.0
        if elapsed < 0:
            elapsed = 0.0
        tokens = min(capacity, tokens + elapsed * rate)
        if tokens >= requested - 1e-9:
            tokens -= requested
            wait_ms = 0
        else:
            deficit = requested - tokens
            wait_ms = math.ceil((deficit / rate) * 1000.0)
        self.store[key] = (tokens, now)
        return wait_ms

    async def eval(
        self, script: str, numkeys: int, key: str, rate: float, capacity: float,
        requested: float, ttl: int,
    ) -> int:
        self.eval_calls += 1
        return self._run(key, float(rate), float(capacity), float(requested))

    async def evalsha(
        self, sha: str, numkeys: int, key: str, rate: float, capacity: float,
        requested: float, ttl: int,
    ) -> int:
        self.evalsha_calls += 1
        if self.noscript_once:
            self.noscript_once = False
            raise RuntimeError("NOSCRIPT No matching script. Please use EVAL.")
        return self._run(key, float(rate), float(capacity), float(requested))

    async def script_load(self, script: str) -> str:
        self.script_load_calls += 1
        return "deadbeef"

    async def aclose(self) -> None:
        return None


def _bucket(
    fake: _FakeAsyncRedis,
    clock: _FakeClock,
    *,
    key: str = "selfevals:ratelimit:ws:provider:anthropic",
    rate_per_sec: float = 1.0,
    capacity: float = 2.0,
    fail_open: bool = True,
) -> RedisTokenBucket:
    """Build a bucket around the injected fake client and deterministic clock."""
    return RedisTokenBucket(
        redis_url="redis://unused",
        key=key,
        rate_per_sec=rate_per_sec,
        capacity=capacity,
        fail_open=fail_open,
        sleep=clock.sleep,
        client=fake,
    )


@pytest.mark.asyncio
async def test_fresh_key_acquires_immediately() -> None:
    clock = _FakeClock()
    fake = _FakeAsyncRedis(clock)
    bucket = _bucket(fake, clock, capacity=2.0)
    await bucket.acquire()
    assert clock.slept == []  # fresh bucket starts full


@pytest.mark.asyncio
async def test_burst_then_throttle() -> None:
    clock = _FakeClock()
    fake = _FakeAsyncRedis(clock)
    bucket = _bucket(fake, clock, rate_per_sec=1.0, capacity=2.0)
    await bucket.acquire()  # free
    await bucket.acquire()  # free (capacity 2)
    await bucket.acquire()  # empty → wait 1 token / 1 per sec = 1.0s, then retry
    assert clock.slept == [pytest.approx(1.0)]


@pytest.mark.asyncio
async def test_miss_does_not_double_charge() -> None:
    clock = _FakeClock()
    fake = _FakeAsyncRedis(clock)
    key = "k"
    bucket = _bucket(fake, clock, key=key, rate_per_sec=1.0, capacity=1.0)
    await bucket.acquire()  # consumes the only token
    # A miss must leave tokens at 0 (not negative): it returns a wait, no consume.
    wait = fake._run(key, 1.0, 1.0, 1.0)
    assert wait > 0
    assert fake.store[key][0] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_lazy_refill_clamps_to_capacity() -> None:
    clock = _FakeClock()
    fake = _FakeAsyncRedis(clock)
    key = "k"
    bucket = _bucket(fake, clock, key=key, rate_per_sec=1.0, capacity=5.0)
    await bucket.acquire()  # 5 → 4
    clock.now_ms += 100_000  # 100s elapsed → +100 tokens, but clamp at 5
    await bucket.acquire()  # refills to 5, then consumes 1 → 4
    assert fake.store[key][0] == pytest.approx(4.0)
    assert clock.slept == []  # never had to wait


@pytest.mark.asyncio
async def test_two_buckets_share_global_budget() -> None:
    clock = _FakeClock()
    fake = _FakeAsyncRedis(clock)  # one store == one global bucket in Redis
    key = "shared"
    a = _bucket(fake, clock, key=key, rate_per_sec=1.0, capacity=2.0)
    b = _bucket(fake, clock, key=key, rate_per_sec=1.0, capacity=2.0)
    await a.acquire()  # 2 → 1
    await b.acquire()  # 1 → 0  (shared!)
    await a.acquire()  # empty → must wait, proving the budget is shared
    assert clock.slept == [pytest.approx(1.0)]


@pytest.mark.asyncio
async def test_fail_open_lets_request_through() -> None:
    clock = _FakeClock()
    fake = _FakeAsyncRedis(clock)
    fake.raise_on_eval = ConnectionError("redis down")
    bucket = _bucket(fake, clock, fail_open=True)
    await bucket.acquire()  # must not raise
    assert clock.slept == []


@pytest.mark.asyncio
async def test_fail_closed_raises() -> None:
    clock = _FakeClock()
    fake = _FakeAsyncRedis(clock)
    fake.raise_on_eval = ConnectionError("redis down")
    bucket = _bucket(fake, clock, fail_open=False)
    with pytest.raises(ConnectionError):
        await bucket.acquire()


@pytest.mark.asyncio
async def test_evalsha_noscript_falls_back_to_eval() -> None:
    clock = _FakeClock()
    fake = _FakeAsyncRedis(clock)
    bucket = _bucket(fake, clock)
    # Prime the cached SHA so the next acquire tries EVALSHA first.
    bucket._sha = "deadbeef"
    fake.noscript_once = True
    await bucket.acquire()
    assert fake.evalsha_calls == 1  # tried EVALSHA, got NOSCRIPT
    assert fake.eval_calls == 1  # fell back to EVAL
    assert fake.script_load_calls == 1  # re-cached the sha
    assert bucket._sha == "deadbeef"


@pytest.mark.asyncio
async def test_drop_in_to_rate_limited_adapter() -> None:
    clock = _FakeClock()
    fake = _FakeAsyncRedis(clock)
    bucket = _bucket(fake, clock, capacity=2.0)
    inner = _CountingAdapter()
    adapter = RateLimitedAdapter(inner, bucket)
    await adapter.invoke(_req())
    await adapter.invoke(_req())
    assert inner.calls == 2


@pytest.mark.asyncio
async def test_adapter_aclose_closes_bucket() -> None:
    bucket = _ClosableBucket()
    adapter = RateLimitedAdapter(_CountingAdapter(), bucket)
    await adapter.aclose()
    assert bucket.closed == [True]


def test_constructor_rejects_bad_args() -> None:
    # The validation runs before the redis import, so these raise without the extra.
    with pytest.raises(ValueError):
        RedisTokenBucket(redis_url="redis://x", key="k", rate_per_sec=0.0, capacity=1.0)
    with pytest.raises(ValueError):
        RedisTokenBucket(redis_url="redis://x", key="k", rate_per_sec=1.0, capacity=0.0)
