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
import os
import warnings
from collections.abc import Iterator
from urllib.parse import urlparse

import pytest
from pytest_postgresql import factories

# ---------------------------------------------------------------------------
# Postgres test fixtures.
#
# The app is Postgres-only. Tests run against a real Postgres reachable at
# SELFEVALS_TEST_POSTGRES_URL (the docker-compose instance on :5433 locally, a
# service container in CI). pytest-postgresql's `noproc` factory clones a fresh
# database per test against that server, so every test is fully isolated and the
# template server is never mutated. We can't use the self-spawning `postgresql_proc`
# factory because the local Homebrew install ships libpq only (no server binary).
# ---------------------------------------------------------------------------

_DEFAULT_TEST_PG = "postgresql://selfevals:selfevals@localhost:5433/selfevals"


def _pg_parts() -> dict[str, object]:
    url = os.environ.get("SELFEVALS_TEST_POSTGRES_URL", _DEFAULT_TEST_PG)
    parsed = urlparse(url)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "user": parsed.username or "postgres",
        "password": parsed.password or "",
    }


_parts = _pg_parts()
# `dbname` is the per-test database pytest-postgresql creates and drops; it must
# NOT be the operator's live database, so use a dedicated test name regardless of
# what database the connection URL points at.
postgresql_noproc = factories.postgresql_noproc(
    host=str(_parts["host"]),
    port=int(_parts["port"]),  # type: ignore[arg-type]
    user=str(_parts["user"]),
    password=str(_parts["password"]),
    dbname="selfevals_pytest",
)
postgresql = factories.postgresql("postgresql_noproc")


@pytest.fixture
def db_url(postgresql: object) -> str:
    """A Postgres DSN for a fresh, isolated per-test database.

    Pass this where the code wants a storage URL (``open_storage(db_url)``,
    ``build_app(db_path=db_url)``, CLI ``--db db_url``).
    """
    info = postgresql.info  # type: ignore[attr-defined]
    return (
        f"postgresql://{info.user}:{_parts['password']}@{info.host}:{info.port}/{info.dbname}"
    )


@pytest.fixture
def storage(db_url: str) -> Iterator[object]:
    """An open PostgresStorage against a fresh per-test database (migrations applied)."""
    from selfevals.storage.factory import open_storage

    store = open_storage(db_url)
    try:
        yield store
    finally:
        store.close()


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
