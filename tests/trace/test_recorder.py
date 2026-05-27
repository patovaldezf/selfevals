from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from selfevals.schemas.enums import SandboxMode, StopReason, ToolCallStatus, TraceState
from selfevals.schemas.trace import (
    AgentSnapshotRef,
    AgentTurnSpan,
    LLMCallSpan,
    RunInfo,
    ToolCallSpan,
    ToolUseRequest,
)
from selfevals.storage.filesystem import FilesystemObjectStore
from selfevals.trace.payload_router import PayloadRouter
from selfevals.trace.recorder import TraceRecorder

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"
T0 = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)


def _recorder(tmp_path: Path) -> TraceRecorder:
    return TraceRecorder(
        workspace_id=WS,
        run=RunInfo(run_id="run_01"),
        agent=AgentSnapshotRef(agent_id="ag_x", agent_version=1),
        framework_version="selfevals/0.0.3",
        runtime="python-3.12",
        sandbox=SandboxMode.MOCK,
        environment_started_at=T0,
        payload_router=PayloadRouter(FilesystemObjectStore(tmp_path), workspace_id=WS),
    )


def test_recorder_builds_minimal_trace(tmp_path: Path) -> None:
    with _recorder(tmp_path) as rec:
        rec.complete()
    trace = rec.build()
    assert trace.final_state.status == TraceState.COMPLETED
    assert trace.spans == []


def test_agent_turn_captures_parent_child_chain(tmp_path: Path) -> None:
    rec = _recorder(tmp_path)
    with rec, rec.agent_turn("turn-1"):
        with rec.llm_call("resolve", provider="anthropic", model="claude-sonnet-4-6") as llm:
            llm.set_output(
                stop_reason=StopReason.TOOL_USE,
                tool_use_requested=[ToolUseRequest(tool="search", tool_use_id="toolu_01")],
            )
            llm.add_tokens(input=100, output=20, total=120)
        with rec.tool_call(
            "search",
            tool_name="search",
            tool_use_id="toolu_01",
        ) as tool:
            tool.status = ToolCallStatus.OK
    trace = rec.build()
    assert len(trace.spans) == 3
    by_kind = {type(s): s for s in trace.spans}
    turn = by_kind[AgentTurnSpan]
    llm_span = by_kind[LLMCallSpan]
    tool_span = by_kind[ToolCallSpan]
    assert llm_span.parent_id == turn.id
    assert tool_span.parent_id == turn.id
    # tool_use_id linkage holds — schema validator passes on build().
    assert tool_span.tool_use_id == "toolu_01"


def test_recorder_accumulates_metrics(tmp_path: Path) -> None:
    rec = _recorder(tmp_path)
    with rec, rec.agent_turn("turn"):
        with rec.llm_call("m", provider="anthropic", model="claude-sonnet-4-6") as llm:
            llm.add_tokens(input=10, output=5, total=15)
        with rec.llm_call("m", provider="anthropic", model="claude-sonnet-4-6") as llm:
            llm.add_tokens(input=20, output=10, total=30)
            llm.retries = 1
        with rec.tool_call("t", tool_name="search"):
            pass
    trace = rec.build()
    assert trace.metrics.llm_call_count == 2
    assert trace.metrics.tool_call_count == 1
    assert trace.metrics.total_tokens_in == 30
    assert trace.metrics.total_tokens_out == 15
    assert trace.metrics.retries == 1


def test_tool_call_exception_marks_span_errored(tmp_path: Path) -> None:
    rec = _recorder(tmp_path)
    import pytest

    with rec, pytest.raises(RuntimeError), rec.tool_call("t", tool_name="boom"):
        raise RuntimeError("kaboom")
    trace = rec.build()
    tool_span = next(s for s in trace.spans if isinstance(s, ToolCallSpan))
    assert tool_span.status == ToolCallStatus.ERROR
    assert "kaboom" in (tool_span.error or "")


def test_recorder_complete_after_exception_marks_errored(tmp_path: Path) -> None:
    import pytest

    rec = _recorder(tmp_path)
    with pytest.raises(ValueError), rec:
        raise ValueError("upstream failed")
    trace = rec.build()
    assert trace.final_state.status == TraceState.ERRORED
    assert "upstream failed" in (trace.final_state.error or "")


def test_recorder_explicit_abort(tmp_path: Path) -> None:
    rec = _recorder(tmp_path)
    with rec:
        rec.abort("user cancelled")
    trace = rec.build()
    assert trace.final_state.status == TraceState.ABORTED
    assert trace.final_state.error == "user cancelled"


def test_orphan_tool_call_without_llm_request_rejected(tmp_path: Path) -> None:
    rec = _recorder(tmp_path)
    import pytest
    from pydantic import ValidationError

    with rec, rec.tool_call("orphan", tool_name="x", tool_use_id="toolu_lone"):
        pass
    with pytest.raises(ValidationError, match="toolu_lone"):
        rec.build()
