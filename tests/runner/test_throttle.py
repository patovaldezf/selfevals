from __future__ import annotations

import asyncio

import pytest

from selfevals.runner.adapters import AdapterRequest, AdapterResponse, AgentAdapter
from selfevals.runner.throttle import AsyncTokenBucket, ProviderThrottle, RateLimitedAdapter

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _req() -> AdapterRequest:
    return AdapterRequest(workspace_id=WS, case_id="ec_x", input={})


class _FakeClock:
    """Manual clock; `sleep` advances it so the bucket refills deterministically."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def time(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.slept.append(delay)
        self.now += delay


class _CountingAdapter(AgentAdapter):
    def __init__(self) -> None:
        self.calls = 0
        self.agent = None

    async def invoke(self, request: AdapterRequest) -> AdapterResponse:
        self.calls += 1
        return AdapterResponse(content="ok")


@pytest.mark.asyncio
async def test_burst_within_capacity_does_not_sleep() -> None:
    clock = _FakeClock()
    bucket = AsyncTokenBucket(rate_per_sec=5.0, capacity=3.0, clock=clock.time, sleep=clock.sleep)
    for _ in range(3):
        await bucket.acquire()
    assert clock.slept == []  # capacity=3 → first 3 acquisitions are free


@pytest.mark.asyncio
async def test_over_capacity_sleeps() -> None:
    clock = _FakeClock()
    bucket = AsyncTokenBucket(rate_per_sec=5.0, capacity=2.0, clock=clock.time, sleep=clock.sleep)
    await bucket.acquire()
    await bucket.acquire()
    await bucket.acquire()  # bucket empty → must wait 1 token / 5 per sec = 0.2s
    assert clock.slept == [pytest.approx(0.2)]


@pytest.mark.asyncio
async def test_rate_cap_over_many_acquires() -> None:
    clock = _FakeClock()
    bucket = AsyncTokenBucket(rate_per_sec=10.0, capacity=1.0, clock=clock.time, sleep=clock.sleep)
    for _ in range(11):
        await bucket.acquire()
    # 1 free (capacity) + 10 throttled at 0.1s each = 1.0s total elapsed.
    assert clock.now == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_shared_bucket_across_concurrent_callers() -> None:
    # Real asyncio.sleep here but tiny: two coroutines share one bucket and the
    # combined throughput respects the rate (both must pass through one bucket).
    bucket = AsyncTokenBucket(rate_per_sec=1000.0, capacity=1.0)
    adapter = RateLimitedAdapter(_CountingAdapter(), bucket)

    async def hit() -> None:
        await adapter.invoke(_req())

    await asyncio.gather(*(hit() for _ in range(5)))
    assert adapter._inner.calls == 5  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_rate_limited_adapter_acquires_then_invokes() -> None:
    clock = _FakeClock()
    bucket = AsyncTokenBucket(rate_per_sec=5.0, capacity=1.0, clock=clock.time, sleep=clock.sleep)
    inner = _CountingAdapter()
    adapter = RateLimitedAdapter(inner, bucket)
    await adapter.invoke(_req())
    await adapter.invoke(_req())  # second needs a refill → sleeps 0.2s
    assert inner.calls == 2
    assert clock.slept == [pytest.approx(0.2)]


def test_provider_throttle_keys_per_provider() -> None:
    a = AsyncTokenBucket(rate_per_sec=1.0, capacity=1.0)
    default = AsyncTokenBucket(rate_per_sec=1.0, capacity=1.0)
    throttle = ProviderThrottle({"anthropic": a, None: default})
    assert throttle.bucket_for("anthropic") is a
    assert throttle.bucket_for("openai") is default  # falls back to default
    assert throttle.bucket_for(None) is default


def test_token_bucket_rejects_bad_args() -> None:
    with pytest.raises(ValueError):
        AsyncTokenBucket(rate_per_sec=0.0, capacity=1.0)
    with pytest.raises(ValueError):
        AsyncTokenBucket(rate_per_sec=1.0, capacity=0.0)
