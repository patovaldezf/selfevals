"""API tests for the error-analysis bundle/ingest endpoints (loop-closer 2C).

The bundle/result bodies are the analysis/schemas.py domain models passed
through as JSON. These pin the endpoint wiring: an empty bundle for a fresh
experiment, and an ingest that proposes a candidate mode (no assignments, so no
Trace seeding needed) which then surfaces in the failure-modes list.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from selfevals.api.app import build_app
from selfevals.runner.launch import ensure_workspace_by_id
from selfevals.storage.factory import open_storage

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"
EXP = "exp_01HCCCCCCCCCCCCCCCCCCCCCCC"


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db = tmp_path / "selfevals.sqlite"
    storage = open_storage(f"sqlite:///{db}")
    try:
        ensure_workspace_by_id(storage, WS)
    finally:
        storage.close()
    return TestClient(build_app(db_path=str(db)))


def test_bundle_empty_for_fresh_experiment(client: TestClient) -> None:
    resp = client.get(f"/api/workspaces/{WS}/experiments/{EXP}/analysis/bundle")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["experiment_id"] == EXP
    assert body["traces"] == []


def test_ingest_creates_candidate_mode(client: TestClient) -> None:
    result = {
        "proposed_modes": [
            {
                "slug": "invented_price",
                "title": "Invented price",
                "definition": "The agent fabricated a price absent from the source.",
            }
        ]
    }
    resp = client.post(
        f"/api/workspaces/{WS}/experiments/{EXP}/analysis/ingest", json=result
    )
    assert resp.status_code == 200, resp.text
    summary = resp.json()
    assert len(summary["created_candidates"]) == 1

    # The new candidate shows up in the taxonomy list (loop closes into 2A).
    modes = client.get(
        f"/api/workspaces/{WS}/failure-modes", params={"status": "candidate"}
    ).json()["items"]
    assert any(m["slug"] == "invented_price" for m in modes)


def test_ingest_rejects_malformed_result(client: TestClient) -> None:
    # An assignment with neither mode_id nor new_mode_slug violates the XOR.
    bad = {"assignments": [{"trace_id": "tr_x"}]}
    resp = client.post(
        f"/api/workspaces/{WS}/experiments/{EXP}/analysis/ingest", json=bad
    )
    assert resp.status_code == 422
