"""Worker loop for durable experiment run jobs."""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass

from selfevals.api.run_launcher import execute_run_job
from selfevals.api.run_queue import RedisRunJobQueue


@dataclass(frozen=True)
class RunWorkerConfig:
    storage_url: str
    redis_url: str
    consumer: str | None = None
    once: bool = False
    idle_sleep_seconds: float = 1.0


def run_worker(config: RunWorkerConfig) -> int:
    queue = RedisRunJobQueue(config.redis_url)
    consumer = config.consumer or f"{socket.gethostname()}:{id(config)}"
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
            )
            queue.ack(message.message_id)
            handled = True
            processed += 1
            if config.once:
                return processed
        if config.once:
            return processed
        if not handled:
            time.sleep(config.idle_sleep_seconds)
