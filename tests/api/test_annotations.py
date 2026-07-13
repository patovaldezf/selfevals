"""API tests for trace annotations — human good/bad feedback on a run."""

from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient

from selfevals.api.app import build_app
from selfevals.schemas.enums import SandboxMode, TraceState
from selfevals.schemas.trace import (
    AgentSnapshotRef,
    EnvironmentInfo,
    FinalState,
    RunInfo,
    Trace,
)
from selfevals.schemas.workspace import Workspace
from selfevals.storage.factory import open_storage

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _seed_trace(db_url: str, *, trace_id: str, eval_case_id: str | None) -> None:
    storage = open_storage(db_url)
    try:
        with storage.open(WS) as scope:
            scope.put_entity(Workspace(id=WS, workspace_id=WS, slug="a", name="a"))
            scope.put_entity(
                Trace(
                    id=trace_id,
                    workspace_id=WS,
                    run=RunInfo(run_id=f"run_{trace_id}", eval_case_id=eval_case_id),
                    agent=AgentSnapshotRef(agent_id="ag", agent_version=1),
                    environment=EnvironmentInfo(
                        framework_version="0.15.0",
                        runtime="pytest",
                        sandbox=SandboxMode.DRY_RUN,
                        started_at=datetime.fromisoformat("2026-07-12T12:00:00+00:00"),
                    ),
                    final_state=FinalState(status=TraceState.COMPLETED),
                )
            )
    finally:
        storage.close()


def test_annotate_and_list_by_trace(db_url: str) -> None:
    client = TestClient(build_app(db_path=db_url))
    _seed_trace(db_url, trace_id="tr_ann", eval_case_id="ec_src")

    resp = client.post(
        f"/api/workspaces/{WS}/traces/tr_ann/annotations",
        json={"verdict": "bad", "notes": "no llamó la tool correcta"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["verdict"] == "bad"
    assert body["notes"] == "no llamó la tool correcta"
    assert body["case_id"] == "ec_src"
    assert body["trace_id"] == "tr_ann"

    # A second, opposite verdict from another annotator.
    resp2 = client.post(
        f"/api/workspaces/{WS}/traces/tr_ann/annotations",
        json={"verdict": "good", "annotator_id": "human:pato@example.com"},
    )
    assert resp2.status_code == 200, resp2.text

    listed = client.get(f"/api/workspaces/{WS}/traces/tr_ann/annotations")
    assert listed.status_code == 200, listed.text
    anns = listed.json()["annotations"]
    assert len(anns) == 2
    assert {a["verdict"] for a in anns} == {"good", "bad"}


def test_annotate_by_run_id_and_adhoc_case(db_url: str) -> None:
    """A trace with no source eval_case_id still annotates (sentinel case_id),
    and the trace resolves by run id too."""
    client = TestClient(build_app(db_path=db_url))
    _seed_trace(db_url, trace_id="tr_adhoc", eval_case_id=None)

    resp = client.post(
        f"/api/workspaces/{WS}/traces/run_tr_adhoc/annotations",
        json={"verdict": "good"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["case_id"] == "ec_adhoc"
    assert resp.json()["trace_id"] == "tr_adhoc"


def test_annotate_unknown_trace_404(db_url: str) -> None:
    client = TestClient(build_app(db_path=db_url))
    storage = open_storage(db_url)
    try:
        with storage.open(WS) as scope:
            scope.put_entity(Workspace(id=WS, workspace_id=WS, slug="a", name="a"))
    finally:
        storage.close()
    resp = client.post(
        f"/api/workspaces/{WS}/traces/tr_missing/annotations",
        json={"verdict": "good"},
    )
    assert resp.status_code == 404, resp.text


def test_list_by_case(db_url: str) -> None:
    client = TestClient(build_app(db_path=db_url))
    _seed_trace(db_url, trace_id="tr_c1", eval_case_id="ec_shared")

    client.post(
        f"/api/workspaces/{WS}/traces/tr_c1/annotations",
        json={"verdict": "bad", "notes": "x"},
    )
    listed = client.get(f"/api/workspaces/{WS}/cases/ec_shared/annotations")
    assert listed.status_code == 200, listed.text
    anns = listed.json()["annotations"]
    assert len(anns) == 1
    assert anns[0]["case_id"] == "ec_shared"
