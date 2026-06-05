"""POST .../experiments/run — launch an experiment over HTTP (F1).

Pins the non-blocking contract: a valid spec returns 202 immediately and the
experiment progresses to `completed` on a background thread; bad input is
rejected synchronously (422); an in-flight experiment conflicts (409). The
pingpong example runs fully offline (mock sandbox, grid proposer, deterministic
grader), so these are network-free and deterministic.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from selfevals.api.app import build_app
from selfevals.schemas.enums import ExperimentState
from selfevals.schemas.experiment import Experiment
from selfevals.storage.seed import seed_workspace
from selfevals.storage.sqlite import SQLiteStorage

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
def client(tmp_path: Path) -> tuple[TestClient, str]:
    db = tmp_path / "selfevals.sqlite"
    # The endpoint creates the workspace on demand; we just hand back the path.
    return TestClient(build_app(db_path=str(db))), str(db)


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


def test_run_409_when_active(client: tuple[TestClient, str]) -> None:
    c, db = client
    # Seed an experiment that is already RUNNING, then POST a spec that reuses
    # its id → conflict.
    st = SQLiteStorage(db)
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
