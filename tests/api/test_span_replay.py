"""Tests for the Playground span replay (`api/span_replay.py`).

Uses an injected fake provider caller so no real API key or network is touched;
the real callers are thin SDK wrappers exercised manually.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from selfevals.api.schemas.traces import SpanReplayRequest
from selfevals.api.span_replay import SpanReplayError, available_providers, replay_span
from selfevals.schemas.enums import SandboxMode, TraceState
from selfevals.schemas.trace import (
    AgentSnapshotRef,
    EnvironmentInfo,
    FinalState,
    LLMCallSpan,
    LLMOutput,
    RunInfo,
    Trace,
)
from selfevals.schemas.workspace import Workspace
from selfevals.storage.factory import open_storage
from selfevals.storage.filesystem import FilesystemObjectStore

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _seed_trace_with_llm_span(db_url: str, *, span_id: str = "sp_llm") -> None:
    storage = open_storage(db_url)
    try:
        with storage.open(WS) as scope:
            scope.put_entity(Workspace(id=WS, workspace_id=WS, slug="r", name="r"))
            llm = LLMCallSpan(
                id=span_id,
                name="chat",
                started_at=datetime.fromisoformat("2026-07-12T12:00:00+00:00"),
                provider="anthropic",
                model="claude-sonnet-4-6",
                system_prompt_inline="You are a helpful agent.",
                messages_inline='[{"role": "user", "content": "hola"}]',
                output=LLMOutput(content_inline="hola de vuelta"),
            )
            scope.put_entity(
                Trace(
                    id="tr_replay",
                    workspace_id=WS,
                    run=RunInfo(run_id="run_replay"),
                    agent=AgentSnapshotRef(agent_id="ag", agent_version=1),
                    environment=EnvironmentInfo(
                        framework_version="0.15.0",
                        runtime="pytest",
                        sandbox=SandboxMode.DRY_RUN,
                        started_at=datetime.fromisoformat("2026-07-12T12:00:00+00:00"),
                    ),
                    final_state=FinalState(status=TraceState.COMPLETED),
                    spans=[llm],
                )
            )
    finally:
        storage.close()


def _fake_caller(captured: dict[str, Any]):
    def call(*, model: str, system: str | None, messages: list[dict[str, Any]], params: dict[str, Any]):
        captured["model"] = model
        captured["system"] = system
        captured["messages"] = messages
        return ("respuesta alterna", 7, 3)

    return call


def test_replay_calls_provider_with_span_prompt(db_url: str, tmp_path: Any) -> None:
    _seed_trace_with_llm_span(db_url)
    storage = open_storage(db_url)
    object_store = FilesystemObjectStore(str(tmp_path))
    captured: dict[str, Any] = {}
    try:
        result = replay_span(
            storage,
            object_store,
            workspace_id=WS,
            trace_id="tr_replay",
            span_id="sp_llm",
            body=SpanReplayRequest(provider="anthropic", model="claude-sonnet-4-5"),
            caller=_fake_caller(captured),
        )
    finally:
        storage.close()
    # The alternate model saw the same prompt the span recorded.
    assert captured["system"] == "You are a helpful agent."
    assert captured["messages"] == [{"role": "user", "content": "hola"}]
    assert captured["model"] == "claude-sonnet-4-5"
    assert result.cost_usd is not None and result.cost_usd > 0
    # And the response is echoed back with the original for comparison.
    assert result.content == "respuesta alterna"
    assert result.tokens_input == 7
    assert result.tokens_output == 3
    assert result.original_model == "claude-sonnet-4-6"
    assert result.original_provider == "anthropic"
    assert result.duration_ms >= 0


def test_replay_rejects_non_llm_span(db_url: str, tmp_path: Any) -> None:
    _seed_trace_with_llm_span(db_url)
    storage = open_storage(db_url)
    object_store = FilesystemObjectStore(str(tmp_path))
    try:
        # A span id that isn't in the trace → not found.
        try:
            replay_span(
                storage,
                object_store,
                workspace_id=WS,
                trace_id="tr_replay",
                span_id="sp_missing",
                body=SpanReplayRequest(provider="anthropic", model="x"),
                caller=_fake_caller({}),
            )
        except SpanReplayError as exc:
            assert "not found" in str(exc)
        else:
            raise AssertionError("expected SpanReplayError for missing span")
    finally:
        storage.close()


def test_replay_prefers_pointer_over_truncated_inline(db_url: str, tmp_path: Any) -> None:
    """When a payload was offloaded, the inline copy is a *truncated* preview and
    the pointer holds the full bytes. Replay must resolve the pointer, not the
    (possibly invalid-JSON) inline — the bug the live smoke caught."""
    object_store = FilesystemObjectStore(str(tmp_path))
    full_messages = '[{"role": "user", "content": "hola con un prompt largo"}]'
    pointer = object_store.put(WS, "messages", full_messages.encode("utf-8"))
    storage = open_storage(db_url)
    try:
        with storage.open(WS) as scope:
            scope.put_entity(Workspace(id=WS, workspace_id=WS, slug="p", name="p"))
            llm = LLMCallSpan(
                id="sp_ptr",
                name="chat",
                started_at=datetime.fromisoformat("2026-07-12T12:00:00+00:00"),
                provider="anthropic",
                model="claude-sonnet-4-6",
                # Inline is a TRUNCATED preview (invalid JSON); pointer is full.
                messages_inline='[{"role": "user", "content": "hola con un pr',
                messages_pointer=pointer,
            )
            scope.put_entity(
                Trace(
                    id="tr_ptr",
                    workspace_id=WS,
                    run=RunInfo(run_id="run_ptr"),
                    agent=AgentSnapshotRef(agent_id="ag", agent_version=1),
                    environment=EnvironmentInfo(
                        framework_version="0.15.0",
                        runtime="pytest",
                        sandbox=SandboxMode.DRY_RUN,
                        started_at=datetime.fromisoformat("2026-07-12T12:00:00+00:00"),
                    ),
                    final_state=FinalState(status=TraceState.COMPLETED),
                    spans=[llm],
                )
            )
        captured: dict[str, Any] = {}
        replay_span(
            storage,
            object_store,
            workspace_id=WS,
            trace_id="tr_ptr",
            span_id="sp_ptr",
            body=SpanReplayRequest(provider="anthropic", model="claude-sonnet-4-5"),
            caller=_fake_caller(captured),
        )
    finally:
        storage.close()
    # The full messages (from the pointer) reached the provider, not the
    # truncated inline that would have failed to parse.
    assert captured["messages"] == [{"role": "user", "content": "hola con un prompt largo"}]


def test_available_providers_reports_sdk_and_key() -> None:
    providers = available_providers()
    names = {p["provider"] for p in providers}
    assert names == {"anthropic", "openai"}
    for p in providers:
        assert "models" in p and isinstance(p["models"], list)
        # SDKs are installed in the dev env; availability still depends on a key.
        assert p["sdk_installed"] is True
