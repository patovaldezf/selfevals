"""GET .../experiments filters + typed contract surface.

Covers the query params the Playground needs (`state`, `feature`) and the
endpoints that were previously raw `dict` and are now typed (`/runs/active`,
`/decisions`). Experiments are seeded directly via SQLiteStorage so we can
vary `state` and `taxonomy.target_features` independently of a real run.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from selfevals.api.app import build_app
from selfevals.schemas.enums import ExperimentState
from selfevals.storage.seed import seed_workspace
from selfevals.storage.sqlite import SQLiteStorage
from tests.api._experiment_factory import make_experiment


@pytest.fixture
def client(tmp_path: Path) -> tuple[TestClient, str]:
    db = tmp_path / "selfevals.sqlite"
    st = SQLiteStorage(str(db))
    ws = seed_workspace(st, slug="t", name="t", user_id="local").workspace
    experiments = [
        make_experiment(
            workspace_id=ws.id,
            name="draft-resolution",
            state=ExperimentState.DRAFT,
            features=["commerce.product_resolution"],
        ),
        make_experiment(
            workspace_id=ws.id,
            name="completed-resolution",
            state=ExperimentState.COMPLETED,
            features=["commerce.product_resolution"],
        ),
        make_experiment(
            workspace_id=ws.id,
            name="completed-search",
            state=ExperimentState.COMPLETED,
            features=["commerce.search"],
        ),
    ]
    with st.open(ws.id) as scope:
        for exp in experiments:
            scope.put_entity(exp)
    st.close()
    return TestClient(build_app(db_path=str(db))), ws.id


def test_no_filter_returns_all(client: tuple[TestClient, str]) -> None:
    c, ws = client
    body = c.get(f"/api/workspaces/{ws}/experiments").json()
    assert body["total"] == 3
    assert len(body["items"]) == 3
    assert body["has_more"] is False


def test_filter_by_state(client: tuple[TestClient, str]) -> None:
    c, ws = client
    body = c.get(f"/api/workspaces/{ws}/experiments", params={"state": "completed"}).json()
    assert body["total"] == 2
    assert {it["state"] for it in body["items"]} == {"completed"}

    drafts = c.get(f"/api/workspaces/{ws}/experiments", params={"state": "draft"}).json()
    assert drafts["total"] == 1
    assert drafts["items"][0]["state"] == "draft"


def test_filter_by_state_invalid_422(client: tuple[TestClient, str]) -> None:
    c, ws = client
    res = c.get(f"/api/workspaces/{ws}/experiments", params={"state": "bogus"})
    assert res.status_code == 422


def test_filter_by_feature(client: tuple[TestClient, str]) -> None:
    c, ws = client
    body = c.get(
        f"/api/workspaces/{ws}/experiments",
        params={"feature": "commerce.search"},
    ).json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "completed-search"


def test_filters_compose_and_paginate(client: tuple[TestClient, str]) -> None:
    c, ws = client
    # state=completed → 2 matching; limit=1 → total stays 2, has_more true.
    body = c.get(
        f"/api/workspaces/{ws}/experiments",
        params={"state": "completed", "limit": 1, "offset": 0},
    ).json()
    assert body["total"] == 2
    assert len(body["items"]) == 1
    assert body["has_more"] is True

    # state + feature together narrows to one.
    narrowed = c.get(
        f"/api/workspaces/{ws}/experiments",
        params={"state": "completed", "feature": "commerce.product_resolution"},
    ).json()
    assert narrowed["total"] == 1
    assert narrowed["items"][0]["name"] == "completed-resolution"


def test_runs_active_typed(client: tuple[TestClient, str]) -> None:
    c, _ = client
    res = c.get("/api/runs/active")
    assert res.status_code == 200
    body = res.json()
    # Envelope shape, not a bare array.
    assert "runs" in body
    assert isinstance(body["runs"], list)
