"""POST .../experiments/run — launch an experiment over HTTP (F1).

Pins the non-blocking contract: a valid spec returns 202 immediately and the
experiment progresses to `completed` on a background thread; bad input is
rejected synchronously (422); an in-flight experiment conflicts (409). The
pingpong example runs fully offline (mock sandbox, grid proposer, deterministic
grader), so these are network-free and deterministic.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from selfevals.api.app import build_app
from selfevals.schemas.enums import ExperimentState
from selfevals.schemas.experiment import Experiment
from selfevals.storage.factory import open_storage
from selfevals.storage.seed import seed_workspace

REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_EXAMPLE = REPO_ROOT / "evals" / "experiments" / "example_pingpong.yaml"
CASES = REPO_ROOT / "evals" / "datasets" / "pingpong.jsonl"
WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _inline_spec(*, max_iterations: int = 2) -> dict[str, Any]:
    """The pingpong spec as an inline dict: cases embedded, path dropped."""
    raw = yaml.safe_load(REPO_EXAMPLE.read_text())
    rows = [json.loads(line) for line in CASES.read_text().splitlines() if line.strip()]
    raw["dataset"] = {"cases_inline": rows}
    raw["experiment"]["run"]["max_iterations"] = max_iterations
    return raw


@pytest.fixture
def client(db_url: str) -> Iterator[tuple[TestClient, str]]:
    # The endpoint creates the workspace on demand; we just hand back the URL.
    yield TestClient(build_app(db_path=db_url)), db_url
    # In-process runs dispatch a daemon thread named `run-<exp>` that holds its
    # own Postgres connection. Join any still-running ones before this test's
    # ephemeral database is dropped, so a late query doesn't hit a killed DB
    # (psycopg AdminShutdown surfacing as an unraisable warning in teardown).
    deadline = time.monotonic() + 20.0
    for thread in threading.enumerate():
        if thread.name.startswith("run-") and thread.is_alive():
            thread.join(timeout=max(0.0, deadline - time.monotonic()))


def _poll_state(c: TestClient, ws: str, exp_id: str, *, timeout: float = 15.0) -> str:
    deadline = time.monotonic() + timeout
    state = ""
    while time.monotonic() < deadline:
        res = c.get(f"/api/workspaces/{ws}/experiments/{exp_id}")
        if res.status_code == 200:
            state = res.json()["summary"]["state"]
            if state in {"completed", "aborted"}:
                return state
        time.sleep(0.1)
    return state


def test_run_inline_spec_returns_202_and_completes(client: tuple[TestClient, str]) -> None:
    c, _ = client
    res = c.post(f"/api/workspaces/{WS}/experiments/run", json={"spec_inline": _inline_spec()})
    assert res.status_code == 202
    body = res.json()
    assert body["workspace_id"] == WS
    exp_id = body["experiment_id"]
    assert exp_id

    # The experiment is visible immediately (persisted before the 202).
    assert c.get(f"/api/workspaces/{WS}/experiments/{exp_id}").status_code == 200
    assert _poll_state(c, WS, exp_id) == "completed"


def test_run_responds_fast(client: tuple[TestClient, str]) -> None:
    # The POST must not block on the loop — it returns well before the run
    # finishes, even with iterations to do.
    c, _ = client
    start = time.monotonic()
    res = c.post(
        f"/api/workspaces/{WS}/experiments/run",
        json={"spec_inline": _inline_spec(max_iterations=4)},
    )
    elapsed = time.monotonic() - start
    assert res.status_code == 202
    assert elapsed < 2.0


def test_run_path_spec_completes(client: tuple[TestClient, str]) -> None:
    c, _ = client
    res = c.post(
        f"/api/workspaces/{WS}/experiments/run",
        json={"spec_path": str(REPO_EXAMPLE), "max_iterations": 2},
    )
    assert res.status_code == 202
    assert _poll_state(c, WS, res.json()["experiment_id"]) == "completed"


def test_run_invalid_spec_422(client: tuple[TestClient, str]) -> None:
    c, _ = client
    # Missing the `experiment:` section entirely.
    res = c.post(
        f"/api/workspaces/{WS}/experiments/run",
        json={"spec_inline": {"dataset": {"cases_inline": [{"input": {}}]}}},
    )
    assert res.status_code == 422


def test_run_zero_cases_422(client: tuple[TestClient, str]) -> None:
    c, _ = client
    spec = _inline_spec()
    spec["dataset"] = {"cases_inline": []}
    res = c.post(f"/api/workspaces/{WS}/experiments/run", json={"spec_inline": spec})
    assert res.status_code == 422


def test_run_requires_exactly_one_source_422(client: tuple[TestClient, str]) -> None:
    c, _ = client
    # Neither source.
    assert c.post(f"/api/workspaces/{WS}/experiments/run", json={}).status_code == 422
    # Both sources.
    both = {"spec_path": str(REPO_EXAMPLE), "spec_inline": _inline_spec()}
    assert c.post(f"/api/workspaces/{WS}/experiments/run", json=both).status_code == 422


def test_run_workspace_path_overrides_spec(client: tuple[TestClient, str]) -> None:
    c, _ = client
    other_ws = "ws_OTHEROTHEROTHEROTHEROTHER"
    spec = _inline_spec()  # spec carries workspace: WS
    res = c.post(f"/api/workspaces/{other_ws}/experiments/run", json={"spec_inline": spec})
    assert res.status_code == 202
    body = res.json()
    assert body["workspace_id"] == other_ws
    # The experiment lands under the path workspace, not the spec's.
    assert c.get(f"/api/workspaces/{other_ws}/experiments/{body['experiment_id']}").status_code == 200


def test_cases_endpoint_lists_persisted_cases(client: tuple[TestClient, str]) -> None:
    """After a run, GET .../cases lists the experiment's eval cases with their
    navigable fields — the fix for "no hay forma de acceder a los cases"."""
    c, _ = client
    res = c.post(f"/api/workspaces/{WS}/experiments/run", json={"spec_inline": _inline_spec()})
    assert res.status_code == 202
    exp_id = res.json()["experiment_id"]
    assert _poll_state(c, WS, exp_id) == "completed"

    cases_res = c.get(f"/api/workspaces/{WS}/experiments/{exp_id}/cases")
    assert cases_res.status_code == 200
    body = cases_res.json()
    assert body["total"] == 2
    assert body["holdout_count"] == 0
    assert len(body["cases"]) == 2
    first = body["cases"][0]
    # The navigable identity + facets the FE renders.
    for key in ("id", "name", "task_type", "input", "graders", "holdout", "is_conversation"):
        assert key in first
    assert first["id"].startswith("ec_")
    # `feature` is an object {primary, secondary}, not a stringified Pydantic repr
    # (the OpenAPI contract now matches what's serialized).
    assert isinstance(first["feature"], dict)
    assert first["feature"]["primary"] == "commerce.product_resolution"
    assert first["feature"]["secondary"] == []
    # Stable order by name.
    names = [c["name"] for c in body["cases"]]
    assert names == sorted(names)


def test_cases_endpoint_links_case_to_trace(client: tuple[TestClient, str]) -> None:
    """With persist_traces=all, each case exposes a resolvable latest trace —
    the fix for "no se puede enlazar case → trace de forma fiable"."""
    c, _ = client
    res = c.post(
        f"/api/workspaces/{WS}/experiments/run",
        json={"spec_inline": _inline_spec(), "persist_traces": "all"},
    )
    assert res.status_code == 202
    exp_id = res.json()["experiment_id"]
    assert _poll_state(c, WS, exp_id) == "completed"

    body = c.get(f"/api/workspaces/{WS}/experiments/{exp_id}/cases").json()
    linked = [c2 for c2 in body["cases"] if c2["latest_trace_id"] is not None]
    assert linked, "expected at least one case linked to a persisted trace"
    for case in linked:
        assert case["latest_run_id"] is not None
        # Both the trace id and the run id resolve via the traces endpoint.
        by_trace = c.get(f"/api/workspaces/{WS}/traces/{case['latest_trace_id']}")
        assert by_trace.status_code == 200
        by_run = c.get(f"/api/workspaces/{WS}/traces/{case['latest_run_id']}")
        assert by_run.status_code == 200
        # Same trace resolved either way.
        assert by_trace.json()["id"] == by_run.json()["id"]


def test_cases_endpoint_empty_for_unknown_experiment(client: tuple[TestClient, str]) -> None:
    """An experiment with no persisted cases returns an empty list, not 404 —
    the FE shows an honest empty state."""
    c, _ = client
    res = c.get(f"/api/workspaces/{WS}/experiments/exp_DOESNOTEXIST/cases")
    assert res.status_code == 200
    assert res.json() == {"cases": [], "total": 0, "holdout_count": 0}


def test_run_409_when_active(client: tuple[TestClient, str]) -> None:
    c, db = client
    # Seed an experiment that is already RUNNING, then POST a spec that reuses
    # its id → conflict.
    st = open_storage(db)
    ws = seed_workspace(st, slug="t", name="t", user_id="local").workspace
    spec = _inline_spec()
    exp_id = "exp_PINNEDPINNEDPINNEDPINNED"
    spec["experiment"]["id"] = exp_id
    raw_exp = dict(spec["experiment"])
    raw_exp["workspace_id"] = ws.id
    exp = Experiment(**raw_exp)
    exp.state = ExperimentState.RUNNING
    exp.created_at = datetime(2026, 5, 1, tzinfo=UTC)
    exp.updated_at = exp.created_at
    with st.open(ws.id) as scope:
        scope.put_entity(exp)
    st.close()

    res = c.post(f"/api/workspaces/{ws.id}/experiments/run", json={"spec_inline": spec})
    assert res.status_code == 409


def test_run_dispatch_in_process_thread_without_redis(
    client: tuple[TestClient, str],
) -> None:
    """With no Redis configured, the 202 advertises the in-process dispatch so
    the caller knows it does not need a worker."""
    c, _ = client
    res = c.post(f"/api/workspaces/{WS}/experiments/run", json={"spec_inline": _inline_spec()})
    assert res.status_code == 202
    assert res.json()["dispatch"] == "in-process-thread"


class _FakeRedisQueue:
    """Queue double that records the enqueue without touching Redis.

    `active_consumers` returns 0 to simulate the exact failure mode: a job
    enqueued with no worker listening.
    """

    redis_label = "redis://localhost:6380/15"

    def __init__(self) -> None:
        self.enqueued: list[object] = []

    def enqueue(self, job: object) -> None:
        self.enqueued.append(job)

    def active_consumers(self) -> int:
        return 0


def test_run_dispatch_redis_worker_and_orphan_warning(
    client: tuple[TestClient, str],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """With a Redis queue but no worker consuming, the 202 reports the
    redis-worker dispatch AND the launcher logs the orphan-job warning — the
    signal that was missing when an experiment silently stuck in draft."""
    import logging

    from selfevals.api import run_launcher

    fake = _FakeRedisQueue()
    monkeypatch.setattr(run_launcher, "configured_run_queue", lambda: fake)

    c, _ = client
    with caplog.at_level(logging.WARNING, logger="selfevals.api.run_launcher"):
        res = c.post(
            f"/api/workspaces/{WS}/experiments/run", json={"spec_inline": _inline_spec()}
        )
    assert res.status_code == 202
    assert res.json()["dispatch"] == "redis-worker"
    assert fake.enqueued, "job should have been enqueued to the redis queue"

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("no worker is consuming" in m for m in warnings)
    # The warning names the redis target (with DB) and never leaks credentials.
    assert any("redis://localhost:6380/15" in m for m in warnings)


def test_run_with_dataset_id_override(client: tuple[TestClient, str]) -> None:
    """A standalone dataset (uploaded via the datasets API) can be selected at
    launch via `dataset_id`, without editing the spec's `dataset:` block."""
    c, _ = client
    rows = [json.loads(line) for line in CASES.read_text().splitlines() if line.strip()]

    created = c.post(
        f"/api/workspaces/{WS}/datasets",
        json={"name": "shared", "dataset_type": "capability", "cases": rows},
    )
    assert created.status_code == 201, created.text
    dataset_id = created.json()["id"]

    # The spec still declares inline cases, but the override points the run at
    # the standalone dataset instead.
    res = c.post(
        f"/api/workspaces/{WS}/experiments/run",
        json={"spec_inline": _inline_spec(), "dataset_id": dataset_id},
    )
    assert res.status_code == 202, res.text
    exp_id = res.json()["experiment_id"]
    assert _poll_state(c, WS, exp_id) == "completed"

    # The completed experiment references the dataset we selected.
    detail = c.get(f"/api/workspaces/{WS}/experiments/{exp_id}").json()
    cases_resp = c.get(f"/api/workspaces/{WS}/experiments/{exp_id}/cases").json()
    assert cases_resp["total"] == len(rows)
    assert detail["summary"]["state"] == "completed"
