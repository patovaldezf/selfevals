"""Lease sweeper: crash recovery for run jobs stranded by a dead worker.

A worker killed by OOMKill/SIGKILL never raises, so ``execute_run_job`` never
writes a terminal state and the job sits in ``running`` with a lapsed lease —
the "FE stuck forever" zombie. These tests pin that the sweeper reaps exactly
those jobs (retry-or-dead-letter), leaves healthy and terminal jobs alone, and
re-enqueues retryable ones when a queue is wired.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import yaml
from fastapi.testclient import TestClient

from selfevals._internal.time import utc_now
from selfevals.api.app import build_app
from selfevals.schemas.job import RunJob, RunJobStatus
from selfevals.storage.factory import open_storage
from selfevals.worker.lease_sweeper import sweep_once

REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_EXAMPLE = REPO_ROOT / "evals" / "experiments" / "example_pingpong.yaml"
CASES = REPO_ROOT / "evals" / "datasets" / "pingpong.jsonl"
WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


class _FakeQueue:
    def __init__(self) -> None:
        self.requeued: list[RunJob] = []

    def enqueue(self, job: RunJob) -> None:
        pass

    def requeue(self, job: RunJob) -> None:
        self.requeued.append(job)

    def active_consumers(self) -> int:
        return 1


def _inline_spec() -> dict[str, Any]:
    raw = yaml.safe_load(REPO_EXAMPLE.read_text())
    rows = [json.loads(line) for line in CASES.read_text().splitlines() if line.strip()]
    raw["dataset"] = {"cases_inline": rows}
    raw["experiment"]["run"]["max_iterations"] = 1
    return raw


def _seed_queued_job(db_url: str, monkeypatch: Any) -> str:
    """Create a workspace + a QUEUED RunJob via the launch endpoint (no worker)."""
    monkeypatch.setattr(
        "selfevals.api.run_launcher.configured_run_queue", lambda: _FakeQueue()
    )
    with TestClient(build_app(db_path=db_url)) as client:
        res = client.post(
            f"/api/workspaces/{WS}/experiments/run", json={"spec_inline": _inline_spec()}
        )
        assert res.status_code == 202
        return str(res.json()["job_id"])


def _load_job(db_url: str, job_id: str) -> RunJob:
    storage = open_storage(db_url)
    try:
        with storage.open(WS) as scope:
            job = scope.get_entity(RunJob, job_id)
            assert isinstance(job, RunJob)
            return job
    finally:
        storage.close()


def _force_running_with_lease(db_url: str, job_id: str, *, lease_expires_at: Any) -> None:
    storage = open_storage(db_url)
    try:
        with storage.open(WS) as scope:
            job = scope.get_entity(RunJob, job_id)
            assert isinstance(job, RunJob)
            job.status = RunJobStatus.RUNNING
            job.lease_owner = "dead-worker"
            job.lease_expires_at = lease_expires_at
            scope.put_entity(job)
    finally:
        storage.close()


def test_sweeps_expired_running_job_and_marks_retry(db_url: str, monkeypatch: Any) -> None:
    job_id = _seed_queued_job(db_url, monkeypatch)
    _force_running_with_lease(db_url, job_id, lease_expires_at=utc_now() - timedelta(seconds=1))

    queue = _FakeQueue()
    monkeypatch.setattr("selfevals.worker.lease_sweeper.RedisRunJobQueue", lambda url: queue)

    reaped = sweep_once(db_url, redis_url="redis://x/0")

    assert reaped == 1
    job = _load_job(db_url, job_id)
    # attempt 0 < max_attempts (3) → retryable, not dead-lettered.
    assert job.status == RunJobStatus.FAILED
    assert job.lease_owner is None
    assert len(queue.requeued) == 1
    assert queue.requeued[0].id == job_id


def test_leaves_healthy_lease_untouched(db_url: str, monkeypatch: Any) -> None:
    job_id = _seed_queued_job(db_url, monkeypatch)
    # Lease still valid → not a zombie.
    _force_running_with_lease(db_url, job_id, lease_expires_at=utc_now() + timedelta(seconds=300))

    reaped = sweep_once(db_url)

    assert reaped == 0
    assert _load_job(db_url, job_id).status == RunJobStatus.RUNNING


def test_ignores_terminal_job(db_url: str, monkeypatch: Any) -> None:
    job_id = _seed_queued_job(db_url, monkeypatch)
    storage = open_storage(db_url)
    try:
        with storage.open(WS) as scope:
            job = scope.get_entity(RunJob, job_id)
            assert isinstance(job, RunJob)
            job.mark_succeeded(utc_now())
            scope.put_entity(job)
    finally:
        storage.close()

    reaped = sweep_once(db_url)

    assert reaped == 0
    assert _load_job(db_url, job_id).status == RunJobStatus.SUCCEEDED


def test_dead_letters_when_attempts_exhausted(db_url: str, monkeypatch: Any) -> None:
    job_id = _seed_queued_job(db_url, monkeypatch)
    storage = open_storage(db_url)
    try:
        with storage.open(WS) as scope:
            job = scope.get_entity(RunJob, job_id)
            assert isinstance(job, RunJob)
            job.status = RunJobStatus.RUNNING
            job.attempt = job.max_attempts  # next failure is terminal
            job.lease_owner = "dead-worker"
            job.lease_expires_at = utc_now() - timedelta(seconds=1)
            scope.put_entity(job)
    finally:
        storage.close()

    queue = _FakeQueue()
    monkeypatch.setattr("selfevals.worker.lease_sweeper.RedisRunJobQueue", lambda url: queue)

    reaped = sweep_once(db_url, redis_url="redis://x/0")

    assert reaped == 1
    assert _load_job(db_url, job_id).status == RunJobStatus.DEAD_LETTERED
    assert queue.requeued == []  # dead-lettered jobs are not re-enqueued
