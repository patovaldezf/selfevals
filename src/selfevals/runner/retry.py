"""Retry-with-backoff wrapper for agent adapters.

`RetryingAdapter` wraps any `AgentAdapter` and re-attempts calls that fail with
a *transient* `AdapterError` (429 rate limit, 5xx, timeouts, connection blips —
see `AdapterError.retryable`). Permanent failures (bad request, contract
violation) propagate immediately.

This is adapter-level retry: it resolves per-call blips before the repetition
completes. It is distinct from the durable job retry (`RunJob.attempt`), which
covers the whole run/worker dying. The two must not fight — keep
`max_delay_seconds * (max_retries + 1)` below the worker's lease TTL so backoff
never trips a lease timeout that then escalates to job retry.

Backoff is exponential with **full jitter**: N cases that hit a 429 at the same
instant get independent delays, so they don't re-storm the provider in lockstep.
A provider-supplied `Retry-After` raises the floor (still jittered, still
capped). `sleep` and `rng` are injectable so tests run without real waits.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from selfevals.runner.adapters import AdapterError, AdapterRequest, AdapterResponse, AgentAdapter
from selfevals.schemas.fleet import Agent, ModelRef


@dataclass(frozen=True)
class RetryPolicy:
    """Backoff parameters. `max_retries=0` disables retry (calls pass through)."""

    max_retries: int = 2
    base_delay: float = 0.5
    max_delay: float = 30.0
    multiplier: float = 2.0
    jitter: float = 0.5


class RetryingAdapter(AgentAdapter):
    """Wrap an adapter, retrying transient `AdapterError`s with jittered backoff."""

    def __init__(
        self,
        inner: AgentAdapter,
        policy: RetryPolicy,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self._inner = inner
        self._policy = policy
        self._sleep = sleep
        self._rng = rng if rng is not None else random.Random()

    @property
    def agent(self) -> Agent | None:  # type: ignore[override]
        return self._inner.agent

    @property
    def model(self) -> ModelRef | None:  # type: ignore[override]
        return self._inner.model

    async def invoke(self, request: AdapterRequest) -> AdapterResponse:
        attempt = 0
        while True:
            try:
                return await self._inner.invoke(request)
            except AdapterError as exc:
                if not exc.retryable or attempt >= self._policy.max_retries:
                    raise
                await self._sleep(self._compute_delay(attempt, exc.retry_after_seconds))
                attempt += 1

    def _compute_delay(self, attempt: int, retry_after: float | None) -> float:
        """Full-jitter exponential backoff, with a `Retry-After` floor.

        `expo = min(max_delay, base * multiplier**attempt)`; the sampled delay
        lies in `[expo*(1-jitter), expo]`. When the provider sent `Retry-After`,
        it raises the floor (so we never retry sooner than asked) but we still
        add jitter on top and cap at `max_delay` so a hostile header can't pin
        the run for minutes.
        """
        expo = min(self._policy.max_delay, self._policy.base_delay * self._policy.multiplier**attempt)
        low = expo * (1.0 - self._policy.jitter)
        delay = low + self._rng.random() * (expo - low)
        if retry_after is not None:
            delay = min(self._policy.max_delay, max(delay, retry_after))
        return delay
