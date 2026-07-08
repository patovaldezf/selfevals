"""Worker loop for durable experiment run jobs."""

from __future__ import annotations

import logging
import socket
import time
import uuid
from dataclasses import dataclass

from selfevals.api.run_launcher import execute_run_job
from selfevals.api.run_queue import RedisRunJobQueue
from selfevals.storage.factory import open_storage, storage_url_label

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunWorkerConfig:
    storage_url: str
    redis_url: str
    consumer: str | None = None
    once: bool = False
    idle_sleep_seconds: float = 1.0


def run_worker(config: RunWorkerConfig) -> int:
    queue = RedisRunJobQueue(config.redis_url)
    consumer = config.consumer or f"{socket.gethostname()}:{uuid.uuid4().hex}"
    # One boot line so a worker/API Redis-DB mismatch (e.g. API on /15, worker
    # on /0) is visible immediately instead of presenting as a silently stuck
    # run. Credentials are stripped from both URLs.
    logger.info(
        "run worker online → redis=%s stream=%s group=%s consumer=%s storage=%s",
        queue.redis_label,
        queue.stream,
        queue.group,
        consumer,
        storage_url_label(config.storage_url),
    )
    processed = 0
    while True:
        handled = False
        for message in queue.reclaim_stale(consumer=consumer):
            execute_run_job(
                storage_url=config.storage_url,
                workspace_id=message.workspace_id,
                job_id=message.job_id,
                owner=consumer,
                queue=queue,
                redis_url=config.redis_url,
            )
            queue.ack(message.message_id)
            handled = True
            processed += 1
            if config.once:
                return processed
        for message in queue.consume(consumer=consumer):
            execute_run_job(
                storage_url=config.storage_url,
                workspace_id=message.workspace_id,
                job_id=message.job_id,
                owner=consumer,
                queue=queue,
                redis_url=config.redis_url,
            )
            queue.ack(message.message_id)
            handled = True
            processed += 1
            if config.once:
                return processed
        for workspace_id, job_id in _queued_durable_jobs(config.storage_url):
            ran = execute_run_job(
                storage_url=config.storage_url,
                workspace_id=workspace_id,
                job_id=job_id,
                owner=consumer,
                queue=queue,
                redis_url=config.redis_url,
            )
            handled = handled or ran
            if ran:
                processed += 1
                if config.once:
                    return processed
        if config.once:
            return processed
        if not handled:
            time.sleep(config.idle_sleep_seconds)


def _queued_durable_jobs(storage_url: str, *, limit: int = 100) -> list[tuple[str, str]]:
    storage = open_storage(storage_url)
    try:
        return storage.queued_run_jobs(limit=limit)
    finally:
        storage.close()
