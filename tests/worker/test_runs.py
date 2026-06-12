"""Worker boot observability.

The single most expensive failure mode is a worker bound to a different Redis
DB than the API: the job enqueues, nothing consumes it, and there is no error.
The boot log line is the defense — these tests pin that it is emitted, carries
the redis/stream/group identity, and never leaks credentials.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest

from selfevals.api.run_queue import RUN_JOBS_GROUP, RUN_JOBS_STREAM, redact_url
from selfevals.worker import runs as worker_runs
from selfevals.worker.runs import RunWorkerConfig, run_worker


class _FakeQueue:
    """In-memory stand-in for RedisRunJobQueue (no Redis, no network)."""

    def __init__(self, redis_url: str, **_: object) -> None:
        self.redis_label = redact_url(redis_url)
        self.stream = RUN_JOBS_STREAM
        self.group = RUN_JOBS_GROUP

    def reclaim_stale(self, *, consumer: str) -> Iterator[object]:
        return iter(())

    def consume(self, *, consumer: str) -> Iterator[object]:
        return iter(())


@pytest.fixture
def fake_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker_runs, "RedisRunJobQueue", _FakeQueue)


def test_worker_logs_boot_line(
    fake_queue: None, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="selfevals.worker.runs"):
        run_worker(
            RunWorkerConfig(
                storage_url="sqlite:///tmp/x.sqlite",
                redis_url="redis://localhost:6380/15",
                consumer="host:1",
                once=True,
            )
        )
    boot = [r for r in caplog.records if "run worker online" in r.getMessage()]
    assert len(boot) == 1
    msg = boot[0].getMessage()
    assert "redis://localhost:6380/15" in msg  # the DB number is preserved
    assert RUN_JOBS_STREAM in msg
    assert RUN_JOBS_GROUP in msg
    assert "consumer=host:1" in msg


def test_worker_boot_line_redacts_credentials(
    fake_queue: None, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="selfevals.worker.runs"):
        run_worker(
            RunWorkerConfig(
                storage_url="sqlite:///tmp/x.sqlite",
                redis_url="redis://user:supersecret@localhost:6380/15",
                consumer="host:1",
                once=True,
            )
        )
    full = "\n".join(r.getMessage() for r in caplog.records)
    assert "supersecret" not in full
    assert "user" not in full
    assert "redis://localhost:6380/15" in full  # host + DB survive redaction


def test_redact_url_keeps_db_number() -> None:
    assert redact_url("redis://localhost:6380/15") == "redis://localhost:6380/15"
    assert redact_url("redis://u:p@host:6380/0") == "redis://host:6380/0"
    assert redact_url("redis://host/3") == "redis://host/3"
