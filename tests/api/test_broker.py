"""Unit tests for the in-proc SpanBroker."""

from __future__ import annotations

import asyncio

import pytest

from bootstrap.api.broker import SpanBroker, _Closed


@pytest.mark.asyncio
async def test_subscribe_receives_published_spans() -> None:
    broker = SpanBroker()
    broker.bind_loop(asyncio.get_running_loop())

    received: list[dict[str, object]] = []

    async def consume() -> None:
        async for ev in broker.subscribe("ws_1", "run_a"):
            if isinstance(ev, _Closed):
                return
            received.append(ev)
            if len(received) >= 2:
                broker.close_run_threadsafe("ws_1", "run_a")

    consumer = asyncio.create_task(consume())
    # Yield once so the subscriber is registered before publishes.
    await asyncio.sleep(0)
    broker.publish_threadsafe("ws_1", "run_a", {"name": "first"})
    broker.publish_threadsafe("ws_1", "run_a", {"name": "second"})
    await asyncio.wait_for(consumer, timeout=2.0)
    assert [s["name"] for s in received] == ["first", "second"]


@pytest.mark.asyncio
async def test_late_subscriber_to_closed_run_gets_close_immediately() -> None:
    broker = SpanBroker()
    broker.bind_loop(asyncio.get_running_loop())
    broker.close_run_threadsafe("ws_1", "run_b", "completed")
    # Yield so the close lands.
    await asyncio.sleep(0)

    events: list[object] = []
    async for ev in broker.subscribe("ws_1", "run_b"):
        events.append(ev)
        if isinstance(ev, _Closed):
            break
    assert isinstance(events[-1], _Closed)


@pytest.mark.asyncio
async def test_publish_with_no_subscribers_is_dropped() -> None:
    broker = SpanBroker()
    broker.bind_loop(asyncio.get_running_loop())
    # Nothing should happen / crash.
    broker.publish_threadsafe("ws_1", "run_c", {"name": "ghost"})
    await asyncio.sleep(0)
