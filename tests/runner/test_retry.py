from __future__ import annotations

import random

import pytest

from selfevals.runner.adapters import AdapterError, AdapterRequest, AdapterResponse, AgentAdapter
from selfevals.runner.retry import RetryingAdapter, RetryPolicy

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _req() -> AdapterRequest:
    return AdapterRequest(workspace_id=WS, case_id="ec_x", input={})


class _ScriptedAdapter(AgentAdapter):
    """Adapter that raises the queued errors, then returns a response.

    `calls` counts invocations; `errors` is consumed front-to-back. Once empty,
    returns a success response.
    """

    def __init__(self, errors: list[AdapterError]) -> None:
        self._errors = list(errors)
        self.calls = 0
        self.agent = None

    async def invoke(self, request: AdapterRequest) -> AdapterResponse:
        self.calls += 1
        if self._errors:
            raise self._errors.pop(0)
        return AdapterResponse(content="ok")


def _recording_sleep() -> tuple[list[float], object]:
    recorded: list[float] = []

    async def fake_sleep(delay: float) -> None:
        recorded.append(delay)

    return recorded, fake_sleep


class _FixedRandom(random.Random):
    """random.Random whose `random()` always returns a fixed value.

    Lets the backoff schedule be asserted deterministically: value 1.0 = top of
    the jitter window (delay == expo), 0.0 = bottom.
    """

    def __init__(self, value: float) -> None:
        super().__init__()
        self._value = value

    def random(self) -> float:
        return self._value


@pytest.mark.asyncio
async def test_transient_then_success() -> None:
    inner = _ScriptedAdapter([AdapterError("429", retryable=True)] * 2)
    recorded, sleep = _recording_sleep()
    adapter = RetryingAdapter(inner, RetryPolicy(max_retries=3), sleep=sleep)
    resp = await adapter.invoke(_req())
    assert resp.content == "ok"
    assert inner.calls == 3  # 2 failures + 1 success
    assert len(recorded) == 2  # slept before each retry


@pytest.mark.asyncio
async def test_exhausts_retries_then_propagates() -> None:
    inner = _ScriptedAdapter([AdapterError("429", retryable=True)] * 5)
    recorded, sleep = _recording_sleep()
    adapter = RetryingAdapter(inner, RetryPolicy(max_retries=2), sleep=sleep)
    with pytest.raises(AdapterError, match="429"):
        await adapter.invoke(_req())
    assert inner.calls == 3  # max_retries=2 → 3 total attempts
    assert len(recorded) == 2


@pytest.mark.asyncio
async def test_permanent_fails_fast_without_sleep() -> None:
    inner = _ScriptedAdapter([AdapterError("400", retryable=False)])
    recorded, sleep = _recording_sleep()
    adapter = RetryingAdapter(inner, RetryPolicy(max_retries=3), sleep=sleep)
    with pytest.raises(AdapterError, match="400"):
        await adapter.invoke(_req())
    assert inner.calls == 1  # no retry
    assert recorded == []  # never slept


@pytest.mark.asyncio
async def test_max_retries_zero_never_retries() -> None:
    inner = _ScriptedAdapter([AdapterError("429", retryable=True)])
    recorded, sleep = _recording_sleep()
    adapter = RetryingAdapter(inner, RetryPolicy(max_retries=0), sleep=sleep)
    with pytest.raises(AdapterError):
        await adapter.invoke(_req())
    assert inner.calls == 1
    assert recorded == []


@pytest.mark.asyncio
async def test_backoff_schedule_is_exponential() -> None:
    # rng fixed to 1.0 → delay = expo (top of the jitter window), deterministic.
    inner = _ScriptedAdapter([AdapterError("429", retryable=True)] * 4)
    recorded, sleep = _recording_sleep()
    rng = _FixedRandom(1.0)
    adapter = RetryingAdapter(
        inner,
        RetryPolicy(max_retries=3, base_delay=1.0, multiplier=2.0, max_delay=100.0, jitter=0.5),
        sleep=sleep,
        rng=rng,
    )
    with pytest.raises(AdapterError):
        await adapter.invoke(_req())
    # expo = 1, 2, 4 for attempts 0,1,2 (rng=1.0 → top of window = expo)
    assert recorded == [1.0, 2.0, 4.0]


@pytest.mark.asyncio
async def test_backoff_respects_retry_after() -> None:
    # 3 errors with max_retries=2 → all attempts exhausted, 2 sleeps recorded.
    inner = _ScriptedAdapter([AdapterError("429", retryable=True, retry_after_seconds=7.0)] * 3)
    recorded, sleep = _recording_sleep()
    rng = _FixedRandom(0.0)
    adapter = RetryingAdapter(
        inner,
        RetryPolicy(max_retries=2, base_delay=0.5, max_delay=100.0, jitter=0.5),
        sleep=sleep,
        rng=rng,
    )
    with pytest.raises(AdapterError):
        await adapter.invoke(_req())
    # Each delay must honour Retry-After (>= 7) even though jittered expo is tiny.
    assert recorded and all(d >= 7.0 for d in recorded)


@pytest.mark.asyncio
async def test_retry_after_capped_at_max_delay() -> None:
    # 2 errors with max_retries=1 → exhausts, 1 sleep recorded.
    inner = _ScriptedAdapter([AdapterError("429", retryable=True, retry_after_seconds=9999.0)] * 2)
    recorded, sleep = _recording_sleep()
    adapter = RetryingAdapter(
        inner, RetryPolicy(max_retries=1, max_delay=30.0), sleep=sleep
    )
    with pytest.raises(AdapterError):
        await adapter.invoke(_req())
    # A hostile Retry-After cannot pin the run beyond max_delay.
    assert recorded[0] == 30.0


@pytest.mark.asyncio
async def test_proxies_agent_and_model() -> None:
    inner = _ScriptedAdapter([])
    adapter = RetryingAdapter(inner, RetryPolicy())
    assert adapter.agent is inner.agent
    assert adapter.model is inner.model
