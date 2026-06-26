"""API tests for the failure-mode taxonomy endpoints (loop-closer 2A).

These drive list / promote / retire / merge / edit straight through the HTTP
surface. A freshly seeded workspace already carries an OFFICIAL taxonomy (see
storage/seed.py), so we seed one, add a CANDIDATE, and exercise the promotion
gate + merge invariant the CLI guarantees.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from selfevals.api.app import build_app
from selfevals.schemas.failure_mode import FailureMode
from selfevals.storage.factory import open_storage
from selfevals.storage.seed import seed_failure_taxonomy, seed_workspace

WS_SLUG = "taxo-team"


@pytest.fixture
def ctx(db_url: str) -> tuple[TestClient, str]:
    storage = open_storage(db_url)
    try:
        seeded = seed_workspace(storage, slug=WS_SLUG, name="Taxo", user_id="local")
        ws_id = seeded.workspace.id
        # The canonical OFFICIAL taxonomy (CLI `init` seeds this; do it here).
        seed_failure_taxonomy(storage, workspace_id=ws_id)
        # Add a candidate to promote/retire/merge.
        with storage.open(ws_id) as scope:
            scope.put_entity(
                FailureMode(
                    id=FailureMode.make_id(),
                    workspace_id=ws_id,
                    slug="invented_price",
                    title="Invented price",
                    definition="The agent fabricated a price not present in the source.",
                    proposed_by="agent:test",
                )
            )
    finally:
        storage.close()
    return TestClient(build_app(db_path=db_url)), ws_id


def _candidate(client: TestClient, ws: str) -> dict:
    resp = client.get(f"/api/workspaces/{ws}/failure-modes", params={"status": "candidate"})
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert items, "expected a seeded candidate"
    return items[0]


def test_list_filters_by_status(ctx: tuple[TestClient, str]) -> None:
    client, ws = ctx
    all_modes = client.get(f"/api/workspaces/{ws}/failure-modes").json()["items"]
    candidates = client.get(
        f"/api/workspaces/{ws}/failure-modes", params={"status": "candidate"}
    ).json()["items"]
    assert len(all_modes) > len(candidates)
    assert all(m["status"] == "candidate" for m in candidates)


def test_promote_moves_to_official(ctx: tuple[TestClient, str]) -> None:
    client, ws = ctx
    fm = _candidate(client, ws)
    resp = client.post(f"/api/workspaces/{ws}/failure-modes/{fm['id']}/promote")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "official"


def test_retire_moves_to_retired(ctx: tuple[TestClient, str]) -> None:
    client, ws = ctx
    fm = _candidate(client, ws)
    resp = client.post(f"/api/workspaces/{ws}/failure-modes/{fm['id']}/retire")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "retired"


def test_edit_updates_text(ctx: tuple[TestClient, str]) -> None:
    client, ws = ctx
    fm = _candidate(client, ws)
    resp = client.patch(
        f"/api/workspaces/{ws}/failure-modes/{fm['id']}",
        json={"title": "Hallucinated price"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["title"] == "Hallucinated price"


def test_edit_requires_a_field(ctx: tuple[TestClient, str]) -> None:
    client, ws = ctx
    fm = _candidate(client, ws)
    resp = client.patch(f"/api/workspaces/{ws}/failure-modes/{fm['id']}", json={})
    assert resp.status_code == 422


def test_merge_retires_source_and_moves_examples(ctx: tuple[TestClient, str]) -> None:
    client, ws = ctx
    candidate = _candidate(client, ws)
    official = next(
        m
        for m in client.get(f"/api/workspaces/{ws}/failure-modes").json()["items"]
        if m["status"] == "official"
    )
    resp = client.post(
        f"/api/workspaces/{ws}/failure-modes/{candidate['id']}/merge",
        json={"into_id": official["id"]},
    )
    assert resp.status_code == 200, resp.text
    # Source is now retired with a back-pointer to the destination.
    retired = client.get(
        f"/api/workspaces/{ws}/failure-modes", params={"status": "retired"}
    ).json()["items"]
    src = next(m for m in retired if m["id"] == candidate["id"])
    assert src["superseded_by"] == official["id"]


def test_merge_into_self_rejected(ctx: tuple[TestClient, str]) -> None:
    client, ws = ctx
    fm = _candidate(client, ws)
    resp = client.post(
        f"/api/workspaces/{ws}/failure-modes/{fm['id']}/merge",
        json={"into_id": fm["id"]},
    )
    assert resp.status_code == 422


def test_unknown_mode_is_404(ctx: tuple[TestClient, str]) -> None:
    client, ws = ctx
    resp = client.post(f"/api/workspaces/{ws}/failure-modes/fm_doesnotexist/promote")
    assert resp.status_code == 404
