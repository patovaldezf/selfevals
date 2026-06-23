"""Pre-emptive request throttle for agent adapters.

`RateLimitedAdapter` wraps an adapter and acquires a token from a shared
`AsyncTokenBucket` before each call, capping requests/minute *before* they hit
the provider — the proactive complement to `RetryingAdapter`'s reactive 429
handling. One bucket per run is shared across every concurrent case (the
executor holds a single adapter), so the cap is global, not per-case.

The bucket holds its `asyncio.Lock` only around the refill arithmetic and
releases it *before* sleeping: if it slept under the lock, every case would
serialise and `parallelism` would collapse to 1. Acquire-compute-release-sleep
is the correct shape. `clock`/`sleep` are injectable for tests.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from selfevals.runner.adapters import AdapterRequest, AdapterResponse, AgentAdapter
from selfevals.schemas.fleet import Agent, ModelRef


class AsyncTokenBucket:
    """Async token bucket: refills at `rate_per_sec`, holds up to `capacity`."""

    def __init__(
        self,
        *,
        rate_per_sec: float,
        capacity: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec must be > 0")
        if capacity <= 0:
            raise ValueError("capacity must be > 0")
        self._rate = rate_per_sec
        self._capacity = capacity
        self._clock = clock
        self._sleep = sleep
        self._tokens = capacity
        self._updated = clock()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: float = 1.0) -> None:
        """Block until `tokens` are available, then consume them.

        The wait is computed under the lock (lazy refill against the clock), but
        the actual sleep happens after releasing it so concurrent acquirers don't
        serialise. After sleeping, the loop re-checks — a second waiter may have
        taken the refilled tokens first.
        """
        # Floating-point refill accumulates rounding error; without an epsilon a
        # bucket can hover at 0.9999999998 < 1.0 forever, sleeping ~1e-17 each
        # loop and never progressing. Treat "within epsilon of enough" as enough.
        epsilon = 1e-9
        while True:
            async with self._lock:
                now = self._clock()
                self._tokens = min(self._capacity, self._tokens + (now - self._updated) * self._rate)
                self._updated = now
                if self._tokens >= tokens - epsilon:
                    self._tokens -= tokens
                    return
                deficit = tokens - self._tokens
                wait = deficit / self._rate
            await self._sleep(wait)


class RateLimitedAdapter(AgentAdapter):
    """Wrap an adapter, acquiring a bucket token before each call."""

    def __init__(self, inner: AgentAdapter, bucket: AsyncTokenBucket) -> None:
        self._inner = inner
        self._bucket = bucket

    @property
    def agent(self) -> Agent | None:  # type: ignore[override]
        return self._inner.agent

    @property
    def model(self) -> ModelRef | None:  # type: ignore[override]
        return self._inner.model

    async def invoke(self, request: AdapterRequest) -> AdapterResponse:
        await self._bucket.acquire(1.0)
        return await self._inner.invoke(request)


class ProviderThrottle:
    """Holds one `AsyncTokenBucket` per provider.

    v1 runs one agent/model per experiment, so one bucket suffices — but keying
    by provider now makes multi-provider a drop-in later. `bucket_for(None)`
    returns the default bucket (for embedded agents with no declared provider).
    """

    def __init__(self, buckets: dict[str | None, AsyncTokenBucket]) -> None:
        self._buckets = buckets

    def bucket_for(self, provider: str | None) -> AsyncTokenBucket | None:
        if provider in self._buckets:
            return self._buckets[provider]
        return self._buckets.get(None)
