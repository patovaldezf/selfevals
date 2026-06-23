"""Durable run job behavior for HTTP-launched experiments."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import yaml
from fastapi.testclient import TestClient

from selfevals.api.app import build_app
from selfevals.schemas.job import RunJob, RunJobStatus
from selfevals.storage.factory import open_storage

REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_EXAMPLE = REPO_ROOT / "evals" / "experiments" / "example_pingpong.yaml"
CASES = REPO_ROOT / "evals" / "datasets" / "pingpong.jsonl"
WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


class _FakeQueue:
    def __init__(self) -> None:
        self.enqueued: list[RunJob] = []
        self.requeued: list[RunJob] = []

    def enqueue(self, job: RunJob) -> None:
        self.enqueued.append(job)

    def requeue(self, job: RunJob) -> None:
        self.requeued.append(job)

    def active_consumers(self) -> int:
        return 1


def _inline_spec(*, max_iterations: int = 1) -> dict[str, Any]:
    raw = yaml.safe_load(REPO_EXAMPLE.read_text())
    rows = [json.loads(line) for line in CASES.read_text().splitlines() if line.strip()]
    raw["dataset"] = {"cases_inline": rows}
    raw["experiment"]["run"]["max_iterations"] = max_iterations
    return raw


def _job_status(db_path: str, workspace_id: str, job_id: str) -> str:
    storage = open_storage(db_path)
    try:
        with storage.open(workspace_id) as scope:
            job = scope.get_entity(RunJob, job_id)
            assert isinstance(job, RunJob)
            return str(job.status)
    finally:
        storage.close()


def test_local_fallback_creates_and_completes_run_job(db_url: str) -> None:
    with TestClient(build_app(db_path=db_url)) as client:
        res = client.post(f"/api/workspaces/{WS}/experiments/run", json={"spec_inline": _inline_spec()})
        assert res.status_code == 202
        job_id = res.json()["job_id"]
        assert job_id
        deadline = time.monotonic() + 10
        status = ""
        while time.monotonic() < deadline:
            status = _job_status(db_url, WS, job_id)
            if status == RunJobStatus.SUCCEEDED:
                break
            time.sleep(0.1)
        assert status == RunJobStatus.SUCCEEDED


def test_redis_config_enqueues_without_local_thread(
    db_url: str, monkeypatch: Any
) -> None:
    queue = _FakeQueue()
    monkeypatch.setattr("selfevals.api.run_launcher.configured_run_queue", lambda: queue)
    with TestClient(build_app(db_path=db_url)) as client:
        res = client.post(f"/api/workspaces/{WS}/experiments/run", json={"spec_inline": _inline_spec()})
        assert res.status_code == 202
        body = res.json()
        assert body["job_id"] == queue.enqueued[0].id
        assert _job_status(db_url, WS, body["job_id"]) == RunJobStatus.QUEUED


def test_cancel_queued_job_marks_cancelled_and_aborts_experiment(
    db_url: str, monkeypatch: Any
) -> None:
    queue = _FakeQueue()
    monkeypatch.setattr("selfevals.api.run_launcher.configured_run_queue", lambda: queue)
    with TestClient(build_app(db_path=db_url)) as client:
        run = client.post(f"/api/workspaces/{WS}/experiments/run", json={"spec_inline": _inline_spec()})
        assert run.status_code == 202
        exp_id = run.json()["experiment_id"]
        cancel = client.post(f"/api/workspaces/{WS}/experiments/{exp_id}/cancel")
        assert cancel.status_code == 202
        assert cancel.json()["state"] == RunJobStatus.CANCELLED
        detail = client.get(f"/api/workspaces/{WS}/experiments/{exp_id}")
        assert detail.status_code == 200
        assert detail.json()["summary"]["state"] == "aborted"
