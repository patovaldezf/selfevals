"""TraceRecorder → SpanSink fan-out (the in-process live-streaming seam).

Pins the contract the SSE broker relies on: as a recorder runs, it emits
`on_trace_started` once, one `on_span_finished` per span (in the exact
`SpanSummary` view shape the REST snapshot uses), and `on_trace_finished`
once with the terminal state. A no-op sink (the CLI default) is the absence
of all of this — verified by the recorder still building a correct Trace
without a sink attached.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from selfevals.schemas.enums import SandboxMode, StopReason, TraceState
from selfevals.schemas.trace import AgentSnapshotRef, RunInfo, ToolUseRequest
from selfevals.storage.filesystem import FilesystemObjectStore
from selfevals.trace.payload_router import PayloadRouter
from selfevals.trace.recorder import TraceRecorder

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"
RUN = "run_sink_01"
T0 = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)

# The keys every SpanSummary-shaped view dict must carry (mirrors
# api.schemas.SpanSummary). If the recorder ever emits a different shape,
# the FE would silently fail to render the live span — so we pin it here.
_SUMMARY_KEYS = {"id", "parent_id", "kind", "name", "started_at", "duration_ms", "detail"}


@dataclass
class _CapturingSink:
    """Records every sink call so the test can assert ordering and shape."""

    started: list[tuple[str, str]] = field(default_factory=list)
    spans: list[dict[str, Any]] = field(default_factory=list)
    finished: list[tuple[str, str, str]] = field(default_factory=list)

    def on_trace_started(self, workspace_id: str, run_id: str) -> None:
        self.started.append((workspace_id, run_id))

    def on_span_finished(
        self, workspace_id: str, run_id: str, span_view: dict[str, Any]
    ) -> None:
        self.spans.append(span_view)

    def on_trace_finished(self, workspace_id: str, run_id: str, final_state: str) -> None:
        self.finished.append((workspace_id, run_id, final_state))


def _recorder(tmp_path: Path, sink: Any) -> TraceRecorder:
    return TraceRecorder(
        workspace_id=WS,
        run=RunInfo(run_id=RUN),
        agent=AgentSnapshotRef(agent_id="ag_x", agent_version=1),
        framework_version="selfevals/0.0.3",
        runtime="python-3.12",
        sandbox=SandboxMode.MOCK,
        environment_started_at=T0,
        payload_router=PayloadRouter(FilesystemObjectStore(tmp_path), workspace_id=WS),
        span_sink=sink,
    )


def test_sink_sees_lifecycle_and_every_span(tmp_path: Path) -> None:
    sink = _CapturingSink()
    rec = _recorder(tmp_path, sink)
    with rec, rec.agent_turn("turn-1"):
        with rec.llm_call("resolve", provider="anthropic", model="claude-sonnet-4-6") as llm:
            llm.set_output(
                stop_reason=StopReason.TOOL_USE,
                tool_use_requested=[ToolUseRequest(tool="search", tool_use_id="toolu_01")],
            )
            llm.add_tokens(input=100, output=20, total=120)
        with rec.tool_call("search", tool_name="search", tool_use_id="toolu_01"):
            pass
    rec.complete()

    # trace_started fired once, up front, with the right channel.
    assert sink.started == [(WS, RUN)]

    # One span event per span the recorder produced — and the count matches
    # what build() persists, so live and snapshot can never disagree.
    trace = rec.build()
    assert len(sink.spans) == len(trace.spans) == 3

    # Spans finish innermost-first (llm, tool, then the enclosing turn).
    assert [s["kind"] for s in sink.spans] == ["llm_call", "tool_call", "agent_turn"]

    # Every emitted span carries exactly the SpanSummary shape.
    for view in sink.spans:
        assert set(view) == _SUMMARY_KEYS
    llm_view = sink.spans[0]
    assert llm_view["detail"]["provider"] == "anthropic"
    assert llm_view["detail"]["model"] == "claude-sonnet-4-6"

    # trace_finished fired once with the terminal state the FE drops "live" on.
    assert sink.finished == [(WS, RUN, str(TraceState.COMPLETED))]


def test_sink_reports_error_terminal_state(tmp_path: Path) -> None:
    sink = _CapturingSink()
    rec = _recorder(tmp_path, sink)
    with rec:
        rec.fail("boom")
    assert sink.finished == [(WS, RUN, str(TraceState.ERRORED))]


def test_sink_failure_never_breaks_the_run(tmp_path: Path) -> None:
    """A sink that raises must not corrupt the Trace — the stream is
    best-effort; SQLite is the source of truth."""

    class _ExplodingSink:
        def on_trace_started(self, *_: Any) -> None:
            raise RuntimeError("subscriber blew up")

        def on_span_finished(self, *_: Any) -> None:
            raise RuntimeError("subscriber blew up")

        def on_trace_finished(self, *_: Any) -> None:
            raise RuntimeError("subscriber blew up")

    rec = _recorder(tmp_path, _ExplodingSink())
    with rec, rec.agent_turn("turn-1"):
        pass
    rec.complete()
    trace = rec.build()
    assert trace.final_state.status == TraceState.COMPLETED
    assert len(trace.spans) == 1


def test_no_sink_is_a_clean_noop(tmp_path: Path) -> None:
    """Without a sink the recorder behaves exactly as before (CLI path)."""
    rec = TraceRecorder(
        workspace_id=WS,
        run=RunInfo(run_id=RUN),
        agent=AgentSnapshotRef(agent_id="ag_x", agent_version=1),
        framework_version="selfevals/0.0.3",
        runtime="python-3.12",
        sandbox=SandboxMode.MOCK,
        environment_started_at=T0,
    )
    with rec, rec.agent_turn("turn-1"):
        pass
    rec.complete()
    assert len(rec.build().spans) == 1
