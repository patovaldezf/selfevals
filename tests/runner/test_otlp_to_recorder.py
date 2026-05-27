"""Unit tests for otlp_to_recorder bridging logic."""

from __future__ import annotations

from selfevals._internal.time import utc_now
from selfevals.runner.otlp_to_recorder import DecodedSpan, feed_to_recorder
from selfevals.schemas.enums import SandboxMode
from selfevals.schemas.trace import (
    AgentSnapshotRef,
    LLMCallSpan,
    RunInfo,
    ToolCallSpan,
)
from selfevals.trace.recorder import TraceRecorder


def _fresh_recorder() -> TraceRecorder:
    return TraceRecorder(
        workspace_id="ws_test",
        run=RunInfo(run_id="run_test"),
        agent=AgentSnapshotRef(agent_id="ag", agent_version=1),
        framework_version="test",
        runtime="py",
        sandbox=SandboxMode.MOCK,
        environment_started_at=utc_now(),
    )


def test_feed_empty_returns_zero() -> None:
    rec = _fresh_recorder()
    assert feed_to_recorder([], rec) == 0


def test_feed_llm_span_increments_metrics() -> None:
    rec = _fresh_recorder()
    spans = [
        DecodedSpan(
            payload={
                "name": "llm.call",
                "span_id": "0102030405060708",
                "parent_span_id": None,
                "start_time": 1_000_000_000,
                "end_time": 1_500_000_000,
                "duration_ms": 500,
                "attributes": {
                    "gen_ai.system": "anthropic",
                    "gen_ai.request.model": "claude-sonnet-4-6",
                    "gen_ai.usage.input_tokens": 50,
                    "gen_ai.usage.output_tokens": 10,
                    "gen_ai.usage.cache_read_input_tokens": 5,
                },
            }
        )
    ]
    count = feed_to_recorder(spans, rec)
    assert count == 1
    trace = rec.build()
    assert trace.metrics.llm_call_count == 1
    assert trace.metrics.total_tokens_in == 55  # 50 + 5 cache_read
    assert trace.metrics.total_tokens_out == 10
    assert isinstance(trace.spans[0], LLMCallSpan)


def test_feed_tool_span_classified_as_tool() -> None:
    rec = _fresh_recorder()
    spans = [
        DecodedSpan(
            payload={
                "name": "tool.invoke",
                "span_id": "0102030405060708",
                "parent_span_id": None,
                "start_time": 1_000_000_000,
                "end_time": 1_100_000_000,
                "duration_ms": 100,
                "attributes": {
                    "openinference.span.kind": "TOOL",
                    "tool.name": "search",
                },
            }
        )
    ]
    feed_to_recorder(spans, rec)
    trace = rec.build()
    assert isinstance(trace.spans[0], ToolCallSpan)
    assert trace.spans[0].tool_name == "search"


def test_feed_unknown_kind_falls_back_to_custom() -> None:
    rec = _fresh_recorder()
    spans = [
        DecodedSpan(
            payload={
                "name": "unknown.thing",
                "span_id": "0102030405060708",
                "parent_span_id": None,
                "start_time": 1_000_000_000,
                "end_time": 1_100_000_000,
                "duration_ms": 100,
                "attributes": {"unrelated": "attr"},
            }
        )
    ]
    feed_to_recorder(spans, rec)
    trace = rec.build()
    assert trace.spans[0].name == "unknown.thing"
    # Should not have counted toward llm/tool metrics.
    assert trace.metrics.llm_call_count == 0
    assert trace.metrics.tool_call_count == 0
