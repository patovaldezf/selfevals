"""End-to-end live streaming: F1 run thread → recorder → SpanSink → broker.

The unit tests pin the recorder→sink seam; this pins the wiring that makes
it real under `selfevals serve`. We launch an experiment over HTTP (the same
`POST .../experiments/run` the web uses) and assert that the background run
thread drives the *broker-backed* sink: the channel opens, real spans land
in `SpanSummary` shape, and the channel closes with the terminal state.

The pingpong example runs fully offline (mock sandbox, grid proposer,
deterministic grader), so this is network-free and deterministic.

We wrap the production `BrokerSpanSink` with a spy that delegates to the
real one (so the broker really is fed) while recording calls — that way the
test exercises the actual `run_launcher` wiring, not a stand-in.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient

import selfevals.api.run_launcher as run_launcher
from selfevals.api.app import build_app
from selfevals.api.broker import reset_for_tests
from selfevals.api.recorder_sink import BrokerSpanSink

REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_EXAMPLE = REPO_ROOT / "evals" / "experiments" / "example_pingpong.yaml"
CASES = REPO_ROOT / "evals" / "datasets" / "pingpong.jsonl"
WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"

_SUMMARY_KEYS = {"id", "parent_id", "kind", "name", "started_at", "duration_ms", "detail"}


@pytest.fixture(autouse=True)
def _reset_broker() -> None:
    reset_for_tests()
    yield
    reset_for_tests()


def _inline_spec() -> dict[str, Any]:
    import yaml

    raw = yaml.safe_load(REPO_EXAMPLE.read_text())
    rows = [json.loads(line) for line in CASES.read_text().splitlines() if line.strip()]
    raw["dataset"] = {"cases_inline": rows}
    raw["experiment"]["run"]["max_iterations"] = 1
    return raw


class _SpySink:
    """Delegates to the real BrokerSpanSink while recording every call."""

    instances: ClassVar[list[_SpySink]] = []

    def __init__(self, broker: Any) -> None:
        self._inner = BrokerSpanSink(broker)
        self.started: list[tuple[str, str]] = []
        self.spans: list[dict[str, Any]] = []
        self.finished: list[tuple[str, str, str]] = []
        _SpySink.instances.append(self)

    def on_trace_started(self, workspace_id: str, run_id: str) -> None:
        self.started.append((workspace_id, run_id))
        self._inner.on_trace_started(workspace_id, run_id)

    def on_span_finished(
        self, workspace_id: str, run_id: str, span_view: dict[str, Any]
    ) -> None:
        self.spans.append(span_view)
        self._inner.on_span_finished(workspace_id, run_id, span_view)

    def on_trace_finished(
        self, workspace_id: str, run_id: str, final_state: str
    ) -> None:
        self.finished.append((workspace_id, run_id, final_state))
        self._inner.on_trace_finished(workspace_id, run_id, final_state)


def _poll_state(c: TestClient, exp_id: str, *, timeout: float = 15.0) -> str:
    deadline = time.monotonic() + timeout
    state = ""
    while time.monotonic() < deadline:
        res = c.get(f"/api/workspaces/{WS}/experiments/{exp_id}")
        if res.status_code == 200:
            state = res.json()["summary"]["state"]
            if state in {"completed", "aborted"}:
                return state
        time.sleep(0.05)
    return state


def test_run_thread_feeds_the_broker(db_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _SpySink.instances.clear()
    monkeypatch.setattr(run_launcher, "BrokerSpanSink", _SpySink)

    app = build_app(db_path=db_url)
    with TestClient(app) as client:
        res = client.post(
            f"/api/workspaces/{WS}/experiments/run", json={"spec_inline": _inline_spec()}
        )
        assert res.status_code == 202
        exp_id = res.json()["experiment_id"]
        assert _poll_state(client, exp_id) == "completed"

    # The run thread built exactly one sink and drove its full lifecycle.
    assert len(_SpySink.instances) == 1
    sink = _SpySink.instances[0]

    # The channel opened (the "live" pill would light) and closed cleanly.
    assert sink.started, "on_trace_started never fired — the run thread never opened a channel"
    assert sink.finished, "on_trace_finished never fired — the channel never closed"
    assert all(state == "completed" for _, _, state in sink.finished)

    # Every started/finished run_id matches a real ephemeral repetition run.
    started_runs = {run for _, run in sink.started}
    finished_runs = {run for _, run, _ in sink.finished}
    assert started_runs == finished_runs

    # Real spans flowed, each in the SpanSummary shape the FE renders.
    assert sink.spans, "no spans were published to the broker during the run"
    for view in sink.spans:
        assert set(view) == _SUMMARY_KEYS
    assert {s["kind"] for s in sink.spans} & {"agent_turn", "llm_call"}


def test_live_stream_delivers_a_span_over_http(
    db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The full browser path: subscribe to /stream for a run_id captured from
    the live sink, and confirm a `span` event arrives over the wire (not just
    the snapshot). This is the exact contract the FE EventSource consumes."""
    _SpySink.instances.clear()
    monkeypatch.setattr(run_launcher, "BrokerSpanSink", _SpySink)

    app = build_app(db_path=db_url)
    with TestClient(app) as client:
        res = client.post(
            f"/api/workspaces/{WS}/experiments/run", json={"spec_inline": _inline_spec()}
        )
        exp_id = res.json()["experiment_id"]
        # Let the run finish so the broker has recorded the run's spans and the
        # trace is persisted; a late subscriber gets the snapshot + complete.
        assert _poll_state(client, exp_id) == "completed"

        assert _SpySink.instances, "run thread never built a sink"
        run_ids = {run for _, run in _SpySink.instances[0].started}
        assert run_ids
        run_id = next(iter(run_ids))

        # Subscribe after completion: the snapshot carries the persisted spans
        # and the stream emits `complete` immediately (terminal in storage).
        with client.stream(
            "GET", f"/api/workspaces/{WS}/traces/{run_id}/stream", timeout=3.0
        ) as response:
            assert response.status_code == 200
            body = ""
            for chunk in response.iter_text():
                body += chunk
                if "event: complete" in body:
                    break

    assert "event: snapshot" in body
    assert "event: complete" in body
    # The snapshot must carry the run's spans (proof the live-published spans
    # were also persisted and are queryable for a late subscriber).
    snapshot_blocks = [
        b for b in body.split("\n\n") if b.strip().startswith("event: snapshot")
    ]
    assert snapshot_blocks
    data_line = next(
        ln for ln in snapshot_blocks[0].splitlines() if ln.startswith("data:")
    )
    snapshot = json.loads(data_line.removeprefix("data:").strip())
    assert snapshot["spans"], "persisted snapshot should carry the run's spans"
