"""Roundtrip test for ToolCallSpan inline args/result (schema 1.4.0, m0010).

Guards that `args_inline`/`result_inline` — the small-payload inline mirror added
alongside the existing pointer/hash columns — survive a write→read cycle through
the Postgres trace mappers, and that a tool span carrying a real `error`/`status`
reads back unchanged.
"""

from __future__ import annotations

from datetime import UTC, datetime

from selfevals.schemas.enums import (
    SandboxMode,
    ToolCallStatus,
    TraceState,
)
from selfevals.schemas.trace import (
    AgentSnapshotRef,
    EnvironmentInfo,
    FinalState,
    LLMCallSpan,
    LLMOutput,
    RunInfo,
    ToolCallSpan,
    ToolUseRequest,
    Trace,
)
from selfevals.schemas.workspace import Workspace
from selfevals.storage.factory import open_storage

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _trace() -> Trace:
    started = datetime(2026, 7, 12, 12, 0, 0, tzinfo=UTC)
    llm = LLMCallSpan(
        id="sp_llm",
        name="turn:0",
        started_at=started,
        provider="anthropic",
        model="claude-sonnet-4-6",
        output=LLMOutput(
            tool_use_requested=[ToolUseRequest(tool="web_search", tool_use_id="toolu_1")],
        ),
    )
    tool = ToolCallSpan(
        id="sp_tool",
        parent_id="sp_llm",
        name="web_search",
        started_at=started,
        tool_name="web_search",
        tool_use_id="toolu_1",
        args_inline='{"query": "guardian angel"}',
        args_hash="sha256:aaa",
        result_inline='{"hits": 3}',
        result_hash="sha256:bbb",
        status=ToolCallStatus.OK,
    )
    return Trace(
        id=Trace.make_id(),
        workspace_id=WS,
        run=RunInfo(run_id="run_x"),
        agent=AgentSnapshotRef(agent_id="ag_x", agent_version=1),
        environment=EnvironmentInfo(
            framework_version="0.15.0",
            runtime="embedded",
            sandbox=SandboxMode.MOCK,
            started_at=started,
        ),
        final_state=FinalState(status=TraceState.COMPLETED),
        spans=[llm, tool],
    )


def test_tool_span_inline_roundtrip(db_url: str) -> None:
    storage = open_storage(db_url)
    try:
        with storage.open(WS) as scope:
            scope.put_entity(Workspace(id=WS, workspace_id=WS, slug="ti", name="ti"))
            trace = _trace()
            scope.put_entity(trace)
            loaded = scope.get_entity(Trace, trace.id)
        assert isinstance(loaded, Trace)
        tool = next(s for s in loaded.spans if isinstance(s, ToolCallSpan))
        assert tool.args_inline == '{"query": "guardian angel"}'
        assert tool.result_inline == '{"hits": 3}'
        assert tool.result_hash == "sha256:bbb"
        assert tool.status is ToolCallStatus.OK
    finally:
        storage.close()


def test_tool_span_error_roundtrip(db_url: str) -> None:
    storage = open_storage(db_url)
    try:
        with storage.open(WS) as scope:
            scope.put_entity(Workspace(id=WS, workspace_id=WS, slug="te", name="te"))
            trace = _trace()
            # Flip the tool span to a failure with an error message.
            errored = trace.spans[1].model_copy(
                update={"status": ToolCallStatus.ERROR, "error": "boom", "result_inline": None}
            )
            trace = trace.model_copy(update={"spans": [trace.spans[0], errored]})
            scope.put_entity(trace)
            loaded = scope.get_entity(Trace, trace.id)
        assert isinstance(loaded, Trace)
        tool = next(s for s in loaded.spans if isinstance(s, ToolCallSpan))
        assert tool.status is ToolCallStatus.ERROR
        assert tool.error == "boom"
    finally:
        storage.close()
