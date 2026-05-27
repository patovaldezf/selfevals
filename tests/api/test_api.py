"""Smoke tests for the HTTP bridge.

We pin against the real pingpong example so the test exercises the
same path the web UI will: `selfeval run` populates the SQLite db,
then the API reads back what's there.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from selfeval.api.app import build_app
from selfeval.cli.main import app as cli_app

# Starlette ships a deprecation warning we don't control; harmless for the API.
warnings.filterwarnings(
    "ignore",
    category=DeprecationWarning,
    module="starlette.formparsers",
)


REPO_EXAMPLE = (
    Path(__file__).resolve().parents[2] / "evals" / "experiments" / "example_pingpong.yaml"
)


@pytest.fixture
def seeded_db(tmp_path: Path) -> Path:
    db = tmp_path / "selfeval.sqlite"
    rc = cli_app(["--db", str(db), "run", str(REPO_EXAMPLE), "--max-iterations", "2"])
    assert rc == 0
    return db


@pytest.fixture
def client(seeded_db: Path) -> TestClient:
    return TestClient(build_app(db_path=str(seeded_db)))


def test_health_endpoint(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["db_path"].endswith("selfeval.sqlite")


def test_list_workspaces_returns_seeded(client: TestClient) -> None:
    response = client.get("/api/workspaces")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["workspaces"], list)
    assert len(body["workspaces"]) >= 1
    ws = body["workspaces"][0]
    for key in ("id", "slug", "name", "experiment_count"):
        assert key in ws


def test_workspace_show_404_for_unknown(client: TestClient) -> None:
    response = client.get("/api/workspaces/ws_does_not_exist")
    assert response.status_code == 404


def test_experiment_detail_and_iterations(client: TestClient) -> None:
    ws = client.get("/api/workspaces").json()["workspaces"][0]
    experiments = client.get(f"/api/workspaces/{ws['id']}/experiments").json()
    assert experiments, "expected at least one experiment to be seeded"
    exp = experiments[0]
    assert exp["primary_metric"] == "pass@1"

    detail = client.get(f"/api/workspaces/{ws['id']}/experiments/{exp['id']}").json()
    assert detail["summary"]["id"] == exp["id"]
    assert detail["result"] is not None
    assert detail["result"]["experiment"]["name"] == "pingpong baseline"
    # Iterations have monotonic indices and decision outcomes.
    assert len(detail["iterations"]) == 2
    decisions = {it["decision_outcome"] for it in detail["iterations"]}
    assert decisions <= {
        "keep_candidate",
        "reject",
        "revert",
        "investigate",
        "require_tradeoff_review",
        "spawn_subexperiment",
        "feature_flag",
        None,
    }


def test_decisions_endpoint_has_records(client: TestClient) -> None:
    ws = client.get("/api/workspaces").json()["workspaces"][0]
    experiments = client.get(f"/api/workspaces/{ws['id']}/experiments").json()
    exp = experiments[0]
    decisions = client.get(f"/api/workspaces/{ws['id']}/experiments/{exp['id']}/decisions").json()
    assert len(decisions) == 2
    for d in decisions:
        assert d["outcome"] in {
            "keep_candidate",
            "reject",
            "revert",
            "investigate",
            "require_tradeoff_review",
            "spawn_subexperiment",
            "feature_flag",
        }
        assert d["automated_rationale"]


def test_anchor_set_returns_points(client: TestClient) -> None:
    ws = client.get("/api/workspaces").json()["workspaces"][0]
    points = client.get(f"/api/workspaces/{ws['id']}/anchor-set").json()
    assert isinstance(points, list)
    # The pingpong example completes 2 iterations.
    assert len(points) == 2
    for p in points:
        assert p["primary_metric_name"] == "pass@1"


def test_trace_not_found_for_unknown_id(client: TestClient) -> None:
    ws = client.get("/api/workspaces").json()["workspaces"][0]
    response = client.get(f"/api/workspaces/{ws['id']}/traces/tr_missing")
    assert response.status_code == 404
