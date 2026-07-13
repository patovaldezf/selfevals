"""API test for external trace ingest (voice calls from the Retell server)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from selfevals.api.app import build_app
from selfevals.schemas.workspace import Workspace
from selfevals.storage.factory import open_storage

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _voice_trace_payload() -> dict:
    started = "2026-07-12T12:00:00+00:00"
    return {
        "run": {"run_id": "run_voice_prod"},
        "agent": {"agent_id": "ag_ga", "agent_version": 1},
        "environment": {
            "framework_version": "0.16.0",
            "runtime": "retell",
            "sandbox": "live_canary",
            "started_at": started,
        },
        "final_state": {"status": "completed"},
        "spans": [
            {
                "id": "sp_turn",
                "kind": "agent_turn",
                "name": "voice_turn:0",
                "started_at": started,
            },
            {
                "id": "sp_stt",
                "parent_id": "sp_turn",
                "kind": "stt",
                "name": "stt",
                "started_at": started,
                "provider": "retell",
                "transcript_inline": "agenda una llamada con Ana",
            },
            {
                "id": "sp_tts",
                "parent_id": "sp_turn",
                "kind": "tts",
                "name": "tts",
                "started_at": started,
                "provider": "retell",
                "text_inline": "Listo, la agendé.",
            },
        ],
    }


def _seed_ws(db_url: str) -> None:
    storage = open_storage(db_url)
    try:
        with storage.open(WS) as scope:
            scope.put_entity(Workspace(id=WS, workspace_id=WS, slug="v", name="v"))
    finally:
        storage.close()


def test_ingest_persists_voice_trace(db_url: str) -> None:
    _seed_ws(db_url)
    client = TestClient(build_app(db_path=db_url))
    resp = client.post(
        f"/api/workspaces/{WS}/traces/ingest", json=_voice_trace_payload()
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["run_id"] == "run_voice_prod"
    assert body["span_count"] == 3

    # The ingested trace is now retrievable with its voice spans.
    got = client.get(f"/api/workspaces/{WS}/traces/{body['trace_id']}")
    assert got.status_code == 200, got.text
    kinds = sorted({s["kind"] for s in got.json()["spans"]})
    assert kinds == ["agent_turn", "stt", "tts"]


def test_ingest_rejects_bad_payload(db_url: str) -> None:
    _seed_ws(db_url)
    client = TestClient(build_app(db_path=db_url))
    resp = client.post(f"/api/workspaces/{WS}/traces/ingest", json={"run": {}})
    assert resp.status_code == 422, resp.text
