"""API tests for the datasets endpoints — the three standalone upload modes.

Datasets are created without running an experiment, so these start from an empty
db and drive create (inline / cases_path / multipart upload), list, detail, and
freeze straight through the HTTP surface.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from selfevals.api.app import build_app

warnings.filterwarnings(
    "ignore",
    category=DeprecationWarning,
    module="starlette.formparsers",
)

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


@pytest.fixture
def client(db_url: str) -> TestClient:
    return TestClient(build_app(db_path=db_url))


def _case() -> dict:
    return {
        "name": "say pong",
        "task_type": "echo",
        "input": {"messages": [{"role": "user", "content": "ping"}]},
        "taxonomy": {
            "level": "final_response",
            "feature": {"primary": "commerce.product_resolution"},
            "source": {"type": "handcrafted"},
            "ground_truth": {"methods": ["exact_match"]},
            "dataset_type": "capability",
        },
        "expected": {"must_include": ["pong"]},
    }


def _create_inline(client: TestClient, *, name: str = "golden-v1") -> dict:
    resp = client.post(
        f"/api/workspaces/{WS}/datasets",
        json={"name": name, "dataset_type": "golden", "cases": [_case(), _case()]},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_create_inline_persists_dataset(client: TestClient) -> None:
    body = _create_inline(client)
    assert body["id"].startswith("ds_")
    assert body["status"] == "active"
    assert body["dataset_type"] == "golden"
    assert body["case_count"] == 2
    assert body["manifest_hash"].startswith("sha256:")
    assert len(body["cases"]) == 2
    assert body["statistics"]["total_cases"] == 2


def test_create_from_cases_path(client: TestClient, tmp_path: Path) -> None:
    jsonl = tmp_path / "cases.jsonl"
    jsonl.write_text("\n".join(json.dumps(_case()) for _ in range(3)) + "\n")
    resp = client.post(
        f"/api/workspaces/{WS}/datasets",
        json={"name": "from-path", "dataset_type": "capability", "cases_path": str(jsonl)},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["case_count"] == 3


def test_create_via_multipart_upload(client: TestClient) -> None:
    payload = "\n".join(json.dumps(_case()) for _ in range(2)) + "\n"
    resp = client.post(
        f"/api/workspaces/{WS}/datasets/upload",
        data={"name": "uploaded", "dataset_type": "smoke"},
        files={"file": ("cases.jsonl", payload, "application/x-ndjson")},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["dataset_type"] == "smoke"
    assert body["case_count"] == 2


def test_create_requires_exactly_one_source(client: TestClient) -> None:
    resp = client.post(
        f"/api/workspaces/{WS}/datasets",
        json={"name": "bad", "cases": [_case()], "cases_path": "x.jsonl"},
    )
    assert resp.status_code == 422


def test_list_datasets_paginated_and_filtered(client: TestClient) -> None:
    _create_inline(client, name="a")
    _create_inline(client, name="b")
    resp = client.get(f"/api/workspaces/{WS}/datasets", params={"limit": 1, "offset": 0})
    assert resp.status_code == 200
    page = resp.json()
    assert page["total"] == 2
    assert len(page["items"]) == 1
    assert page["has_more"] is True

    # Filter by a status none of them have yet → empty.
    resp = client.get(f"/api/workspaces/{WS}/datasets", params={"status": "frozen"})
    assert resp.json()["total"] == 0


def test_dataset_detail_and_404(client: TestClient) -> None:
    created = _create_inline(client)
    resp = client.get(f"/api/workspaces/{WS}/datasets/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]

    missing = client.get(f"/api/workspaces/{WS}/datasets/ds_nope")
    assert missing.status_code == 404


def test_freeze_dataset(client: TestClient) -> None:
    created = _create_inline(client)
    resp = client.post(f"/api/workspaces/{WS}/datasets/{created['id']}/freeze")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "frozen"

    # And it now shows under the frozen filter.
    listed = client.get(f"/api/workspaces/{WS}/datasets", params={"status": "frozen"})
    assert listed.json()["total"] == 1


def test_freeze_unknown_dataset_404(client: TestClient) -> None:
    resp = client.post(f"/api/workspaces/{WS}/datasets/ds_nope/freeze")
    assert resp.status_code == 404


def test_workspace_isolation(client: TestClient) -> None:
    _create_inline(client)
    other = client.get("/api/workspaces/ws_other/datasets")
    assert other.status_code == 200
    assert other.json()["total"] == 0
