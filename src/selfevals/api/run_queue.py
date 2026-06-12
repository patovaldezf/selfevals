"""Redis Streams queue for durable experiment run jobs."""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from selfevals.schemas.job import RunJob

REDIS_URL_ENV = "SELFEVALS_REDIS_URL"
RUN_JOBS_STREAM = "selfevals:jobs:runs"
RUN_JOBS_GROUP = "selfevals-workers"

# How long a registered consumer can be idle before we treat it as dead for the
# orphan-job check. A live worker blocks on xreadgroup with a ~5s timeout, so it
# refreshes its idle every few seconds; 60s gives generous margin against GC
# pauses or slow jobs while still aging out a crashed worker quickly.
LIVE_CONSUMER_MAX_IDLE_MS = 60_000


def redact_url(url: str) -> str:
    """Strip credentials from a Redis URL while keeping host, port, and DB.

    The DB number is the part that matters most for debugging worker/API
    mismatches (``/15`` vs ``/0``), so it is preserved verbatim. On any parse
    failure we fall back to a coarse label rather than risk leaking a password
    into logs.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return "redis://<unparseable>"
    scheme = parsed.scheme or "redis"
    host = parsed.hostname or "localhost"
    netloc = f"{host}:{parsed.port}" if parsed.port else host
    return f"{scheme}://{netloc}{parsed.path}"


@dataclass(frozen=True)
class RunJobMessage:
    message_id: str
    job_id: str
    workspace_id: str
    experiment_id: str


class RedisRunJobQueue:
    """Redis Streams-backed queue for run workers."""

    def __init__(
        self,
        redis_url: str,
        *,
        stream: str = RUN_JOBS_STREAM,
        group: str = RUN_JOBS_GROUP,
    ) -> None:
        try:
            import redis
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "Redis run queue requires the redis extra: pip install 'selfevals[redis]'"
            ) from exc
        self._client: Any = redis.Redis.from_url(redis_url, decode_responses=True)
        self.redis_label = redact_url(redis_url)
        self.stream = stream
        self.group = group
        self._ensure_group()

    def enqueue(self, job: RunJob) -> None:
        self._client.xadd(self.stream, _job_fields(job))

    def requeue(self, job: RunJob) -> None:
        self.enqueue(job)

    def ack(self, message_id: str) -> None:
        self._client.xack(self.stream, self.group, message_id)

    def consume(
        self,
        *,
        consumer: str,
        count: int = 1,
        block_ms: int = 5_000,
    ) -> Iterator[RunJobMessage]:
        rows = self._client.xreadgroup(
            self.group,
            consumer,
            {self.stream: ">"},
            count=count,
            block=block_ms,
        )
        for _stream_name, messages in rows:
            for message_id, fields in messages:
                yield _message_from_fields(str(message_id), fields)

    def reclaim_stale(
        self,
        *,
        consumer: str,
        min_idle_ms: int = 60_000,
        count: int = 10,
    ) -> Iterator[RunJobMessage]:
        rows = self._client.xautoclaim(
            self.stream,
            self.group,
            consumer,
            min_idle_ms,
            "0-0",
            count=count,
        )
        for message_id, fields in rows[1]:
            yield _message_from_fields(str(message_id), fields)

    def active_consumers(self, *, max_idle_ms: int = LIVE_CONSUMER_MAX_IDLE_MS) -> int | None:
        """Number of *live* consumers in the run-jobs group.

        Used by the launcher to warn when a job is enqueued but no worker is
        listening. A dead worker leaves its consumer registered in the group
        indefinitely, so a raw `XINFO GROUPS` count reports ghosts as if they
        were workers — exactly the false-negative that lets the orphan warning
        stay silent. Instead we read per-consumer `idle` (ms since that consumer
        last interacted with the group) via `XINFO CONSUMERS` and count only
        those seen within `max_idle_ms`. A live worker blocks on `xreadgroup`
        with a ~5s timeout, so it refreshes its idle well inside the default
        window; a crashed one ages out.

        Returns ``None`` (not zero) if Redis can't be queried, so the caller can
        tell "no live consumers" apart from "couldn't check" and never fails the
        launch over an observability probe.
        """
        try:
            consumers = self._client.xinfo_consumers(self.stream, self.group)
        except Exception:
            # Group may not exist yet, or Redis is unreachable — either way the
            # probe is best-effort and must not break the launch.
            return None
        live = 0
        for consumer in consumers:
            idle = consumer.get("idle")
            if idle is None or int(idle) <= max_idle_ms:
                live += 1
        return live

    def _ensure_group(self) -> None:
        try:
            self._client.xgroup_create(self.stream, self.group, id="0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise


def configured_run_queue() -> RedisRunJobQueue | None:
    redis_url = os.environ.get(REDIS_URL_ENV)
    if not redis_url:
        return None
    return RedisRunJobQueue(redis_url)


def _job_fields(job: RunJob) -> dict[str, str]:
    return {
        "job_id": job.id,
        "workspace_id": job.workspace_id,
        "experiment_id": job.experiment_id,
    }


def _message_from_fields(message_id: str, fields: dict[str, Any]) -> RunJobMessage:
    return RunJobMessage(
        message_id=message_id,
        job_id=str(fields["job_id"]),
        workspace_id=str(fields["workspace_id"]),
        experiment_id=str(fields["experiment_id"]),
    )
