"""Tests for the ambient trace handle (`trace/context.py`).

Covers the two ways an embedded agent reaches the handle — an `async def`
awaited in the same task, and a sync callable run on `asyncio.to_thread` — plus
the no-op behavior outside a bound invocation, and that spans emitted through
the handle nest under the recorder's open parent.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from selfevals.schemas.enums import SandboxMode
from selfevals.schemas.trace import (
    AgentSnapshotRef,
    LLMCallSpan,
    RunInfo,
    ToolCallSpan,
    ToolUseRequest,
)
from selfevals.trace.context import (
    bind_trace_handle,
    get_current_trace_handle,
)
from selfevals.trace.recorder import TraceRecorder


def _recorder(tmp_path: Path) -> TraceRecorder:
    return TraceRecorder(
        workspace_id="ws_ctx",
        run=RunInfo(run_id="run_ctx"),
        agent=AgentSnapshotRef(agent_id="ag", agent_version=1),
        framework_version="0.15.0",
        runtime="embedded",
        sandbox=SandboxMode.MOCK,
    )


def test_handle_is_none_outside_bind() -> None:
    assert get_current_trace_handle() is None


def test_bind_exposes_handle_and_restores(tmp_path: Path) -> None:
    rec = _recorder(tmp_path)
    assert get_current_trace_handle() is None
    with bind_trace_handle(rec) as handle:
        assert get_current_trace_handle() is handle
        assert handle.used is False
    assert get_current_trace_handle() is None


def test_used_flips_on_first_span(tmp_path: Path) -> None:
    rec = _recorder(tmp_path)
    with rec, rec.agent_turn("case:x"), bind_trace_handle(rec) as handle:
        assert handle.used is False
        with handle.llm_call("call", provider="anthropic", model="claude-sonnet-4-6"):
            pass
        assert handle.used is True


def test_handle_propagates_across_to_thread(tmp_path: Path) -> None:
    """A sync callable run on a worker thread still sees the bound handle —
    this is the embedded-sync path (`asyncio.to_thread`)."""
    rec = _recorder(tmp_path)

    def sync_agent() -> bool:
        handle = get_current_trace_handle()
        if handle is None:
            return False
        with handle.tool_call("t", tool_name="search", tool_use_id="toolu_x") as tool:
            tool.args_inline = '{"q": 1}'
        return True

    async def drive() -> bool:
        with rec, rec.agent_turn("case:x"), bind_trace_handle(rec):
            # First request the tool via an LLM span so linkage validates.
            with rec.llm_call("c", provider="p", model="m") as llm:
                llm.set_output(
                    tool_use_requested=[ToolUseRequest(tool="search", tool_use_id="toolu_x")]
                )
            return await asyncio.to_thread(sync_agent)

    saw_handle = asyncio.run(drive())
    assert saw_handle is True
    trace = rec.build()
    tool_spans = [s for s in trace.spans if isinstance(s, ToolCallSpan)]
    assert len(tool_spans) == 1
    assert tool_spans[0].args_inline == '{"q": 1}'


def test_spans_nest_under_open_parent(tmp_path: Path) -> None:
    """Spans emitted through the handle parent to whatever the recorder had
    open (the case turn), so the run tree is real, not flat."""
    rec = _recorder(tmp_path)
    # Nested on purpose: the tool span must parent to the llm span, not the
    # turn — that's exactly what this test asserts, so the with's don't collapse.
    with rec, rec.agent_turn("case:x"), bind_trace_handle(rec) as handle:  # noqa: SIM117
        with handle.llm_call("turn:0", provider="anthropic", model="m") as llm:
            llm.set_output(
                tool_use_requested=[ToolUseRequest(tool="search", tool_use_id="toolu_1")]
            )
            with handle.tool_call("search", tool_name="search", tool_use_id="toolu_1"):
                pass
    trace = rec.build()
    llm = next(s for s in trace.spans if isinstance(s, LLMCallSpan))
    tool = next(s for s in trace.spans if isinstance(s, ToolCallSpan))
    turn = next(s for s in trace.spans if s.name == "case:x")
    # llm parents to the turn; tool parents to the llm.
    assert llm.parent_id == turn.id
    assert tool.parent_id == llm.id


def test_reasoning_marks_available(tmp_path: Path) -> None:
    rec = _recorder(tmp_path)
    with (
        rec,
        rec.agent_turn("case:x"),
        bind_trace_handle(rec) as handle,
        handle.llm_call("turn:0", provider="anthropic", model="m") as llm,
    ):
        llm.set_reasoning(thinking_tokens=42, signature="sig")
    trace = rec.build()
    llm = next(s for s in trace.spans if isinstance(s, LLMCallSpan))
    assert llm.reasoning.available is True
    assert llm.reasoning.thinking_tokens == 42
    assert llm.reasoning.signature == "sig"
