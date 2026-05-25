"""End-to-end SSE: receiver thread → broker → /traces/{run_id}/stream."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from bootstrap.api.app import build_app
from bootstrap.api.broker import get_broker, reset_for_tests
from bootstrap.api.broker_bridge import BrokerPublisher


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
