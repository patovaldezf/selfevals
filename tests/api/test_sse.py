"""End-to-end SSE: receiver thread → broker → /traces/{run_id}/stream."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from selfevals.api.app import build_app
from selfevals.api.broker import get_broker, reset_for_tests
from selfevals.api.broker_bridge import BrokerPublisher
from selfevals.schemas.enums import SandboxMode, TraceState
from selfevals.schemas.trace import (
    AgentSnapshotRef,
    EnvironmentInfo,
    FinalState,
    RunInfo,
    Trace,
)
from selfevals.storage.seed import seed_workspace
from selfevals.storage.sqlite import SQLiteStorage


@pytest.fixture(autouse=True)
def _reset_broker() -> None:
    reset_for_tests()
    yield
    reset_for_tests()


def _parse_events(body: str) -> list[tuple[str, str]]:
    events: list[tuple[str, str]] = []
    for chunk in body.split("\n\n"):
        chunk = chunk.strip()
        if not chunk:
            continue
        event_line = next((ln for ln in chunk.splitlines() if ln.startswith("event:")), None)
        data_line = next((ln for ln in chunk.splitlines() if ln.startswith("data:")), None)
        if event_line and data_line:
            events.append(
                (event_line.removeprefix("event:").strip(), data_line.removeprefix("data:").strip())
            )
    return events


def test_stream_emits_snapshot_and_complete(tmp_path):
    """A subscriber to a closed run gets snapshot (empty) + complete."""
    app = build_app(db_path=str(tmp_path / "db.sqlite"))
    with TestClient(app) as client:
        # The lifespan handler should have bound the broker's loop.
        broker = get_broker()
        publisher = BrokerPublisher(broker)
        # Pre-close the run so the subscriber receives a close event
        # immediately. This exercises the late-subscriber path through
        # the API surface end-to-end.
        publisher.close("ws_x", "run_x", "completed")

        with client.stream(
            "GET",
            "/api/workspaces/ws_x/traces/run_x/stream",
            timeout=2.0,
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            body = ""
            for chunk in response.iter_text():
                body += chunk
                if "event: complete" in body:
                    break

    events = _parse_events(body)
    kinds = [e[0] for e in events]
    assert "snapshot" in kinds
    assert "complete" in kinds
    complete = next(data for kind, data in events if kind == "complete")
    assert json.loads(complete)["final_state"] == "completed"


def test_stream_emits_complete_when_snapshot_is_already_terminal(tmp_path: Path) -> None:
    """Regression: a persisted trace whose `final_state` is already
    `completed` (or any non-`running` value) must trigger `complete`
    immediately from the snapshot, without anyone publishing a close
    to the broker. Without this, the stream stays open forever, the
    FE never sees `complete`, the "live" pill keeps pulsing on a
    finished trace, and the EventSource leaks across navigations.
    """
    db_path = str(tmp_path / "db.sqlite")
    # Seed a finished trace directly into storage. No publisher.close()
    # is called — the only signal that the run is done is the persisted
    # `final_state`.
    st = SQLiteStorage(db_path)
    ws = seed_workspace(st, slug="t", name="t", user_id="local").workspace
    started = datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC)
    trace = Trace(
        id=Trace.make_id(),
        workspace_id=ws.id,
        run=RunInfo(run_id="run_done", experiment_id="exp_1", iteration=0),
        agent=AgentSnapshotRef(agent_id="ag_x", agent_version=1),
        environment=EnvironmentInfo(
            framework_version="test",
            runtime="test",
            sandbox=SandboxMode.MOCK,
            started_at=started,
            ended_at=started + timedelta(seconds=1),
        ),
        final_state=FinalState(status=TraceState.COMPLETED),
    )
    with st.open(ws.id) as scope:
        scope.put_entity(trace)
    st.close()

    app = build_app(db_path=db_path)
    # Note: no publisher.close() — the snapshot alone must drive complete.
    with (
        TestClient(app) as client,
        client.stream(
            "GET",
            f"/api/workspaces/{ws.id}/traces/run_done/stream",
            timeout=2.0,
        ) as response,
    ):
        assert response.status_code == 200
        body = ""
        for chunk in response.iter_text():
            body += chunk
            if "event: complete" in body:
                break

    events = _parse_events(body)
    kinds = [e[0] for e in events]
    assert "snapshot" in kinds, f"expected snapshot in {kinds}"
    assert "complete" in kinds, (
        f"finished trace must emit complete from snapshot alone; got {kinds}"
    )
    complete = next(data for kind, data in events if kind == "complete")
    assert json.loads(complete)["final_state"] == "completed"
