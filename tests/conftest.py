"""Shared pytest fixtures.

`filterwarnings = ["error"]` (see pyproject) turns every warning into an
error. pytest-asyncio creates a fresh event loop per test and hands it to
the GC rather than closing it; when a *later* test triggers garbage
collection, that loop's ``__del__`` emits an "unclosed event loop"
ResourceWarning that the filter then escalates into a spurious failure in
an unrelated test. We close the per-test loop in teardown so the loop is
gone before the GC ever sees it. A genuinely leaked socket inside a test
still warns within that test's own scope, so this does not mask real leaks.
"""

from __future__ import annotations

import asyncio
import gc
import warnings
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _close_event_loop(request: pytest.FixtureRequest) -> Iterator[None]:
    yield
    # Only async tests (pytest-asyncio creates a per-test loop for them) leave a
    # loop behind. For sync tests there is nothing to close, and probing for one
    # would only create a spurious loop.
    if request.node.get_closest_marker("asyncio") is None:
        return
    policy = asyncio.get_event_loop_policy()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        try:
            loop = policy.get_event_loop()
        except RuntimeError:
            return
    if loop.is_closed() or loop.is_running():
        return
    loop.close()
    gc.collect()
