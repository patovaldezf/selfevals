"""Background sweeper that reaps run jobs whose lease has lapsed.

A worker killed mid-run by OOMKill or SIGKILL never raises, so the failure path
in ``execute_run_job`` never runs and the job is stranded in ``running`` with an
expired lease — a zombie that the FE shows as "running forever". The heartbeat
(``_lease_heartbeat`` in ``api/run_launcher``) keeps a *healthy* run's lease
fresh; this sweeper is the other half: it finds jobs whose lease has actually
lapsed and routes them through ``mark_run_job_failed`` for the usual
retry-vs-dead-letter decision, re-enqueuing the retryable ones.

The expiry test lives in the SQL ``WHERE`` (``lease_expires_at < now``), not in a
read-then-write, so a job the heartbeat renews a microsecond later is simply not
returned — the heartbeat and the sweeper cannot both act on the same job.

One sweeper process suffices for an entire cluster; run it as its own deployment
(``selfevals worker sweeper``).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from selfevals._internal.time import utc_now
from selfevals.api.run_jobs import get_run_job, mark_run_job_failed
from selfevals.api.run_queue import RedisRunJobQueue
from selfevals.storage.factory import open_storage, storage_url_label

logger = logging.getLogger(__name__)

_SWEEP_ERROR = "lease expired: worker presumed dead (OOMKill/SIGKILL)"


@dataclass(frozen=True)
class LeaseSweeperConfig:
    storage_url: str
    redis_url: str | None = None
    interval_seconds: float = 30.0
    batch: int = 100
    once: bool = False


def sweep_once(
    storage_url: str, *, redis_url: str | None = None, batch: int = 100
) -> int:
    """Reap one batch of expired-lease jobs. Returns how many were reaped.

    Each expired job is reloaded and passed through ``mark_run_job_failed``;
    retryable jobs are re-enqueued onto the Redis stream when one is configured.
    Reaping is idempotent: a job already moved terminal by its worker is skipped
    by the ``expired_run_job_leases`` status filter.
    """
    storage = open_storage(storage_url)
    queue = RedisRunJobQueue(redis_url) if redis_url else None
    reaped = 0
    try:
        expired = storage.expired_run_job_leases(now=utc_now(), limit=batch)
        for workspace_id, job_id in expired:
            job = get_run_job(storage, workspace_id=workspace_id, job_id=job_id)
            if job is None or job.is_terminal:
                continue
            job, should_retry = mark_run_job_failed(storage, job=job, error=_SWEEP_ERROR)
            if should_retry and queue is not None:
                queue.requeue(job)
            reaped += 1
            logger.info(
                "swept expired-lease job %s (retry=%s, attempt=%d/%d)",
                job_id,
                should_retry,
                job.attempt,
                job.max_attempts,
            )
    finally:
        storage.close()
    return reaped


def run_lease_sweeper(config: LeaseSweeperConfig) -> int:
    """Long-lived loop: sweep expired leases every ``interval_seconds``.

    Returns the total reaped (meaningful only with ``once=True``, used by tests
    and one-shot invocations).
    """
    logger.info(
        "lease sweeper online → storage=%s interval=%.0fs redis=%s",
        storage_url_label(config.storage_url),
        config.interval_seconds,
        RedisRunJobQueue(config.redis_url).redis_label if config.redis_url else "none",
    )
    total = 0
    while True:
        try:
            total += sweep_once(
                config.storage_url, redis_url=config.redis_url, batch=config.batch
            )
        except Exception:  # pragma: no cover - a bad sweep must not kill the loop
            logger.warning("lease sweep failed; will retry next interval", exc_info=True)
        if config.once:
            return total
        time.sleep(config.interval_seconds)
