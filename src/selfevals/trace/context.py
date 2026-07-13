"""Ambient trace handle for in-process (embedded) agents.

An embedded agent runs *inside* selfevals' process, driven by `EmbeddedAdapter`.
Today its whole reply is reconstructed into one synthetic `adapter_response` LLM
span (see `runner/trace_response_writer.py`) — it can't show the agent's real
per-call structure (each LLM call, each tool call, the model's reasoning, the
latency of one call).

This module lets an embedded agent emit *real* spans into the live recorder
without threading a recorder object through its own call stack (which would
couple the agent's public API to selfevals internals). The executor binds the
active recorder to a `ContextVar` for the duration of `adapter.invoke()`; the
agent's selfevals shim reads it back with `get_current_trace_handle()` and calls
the same context managers the recorder exposes — so spans nest correctly under
the executor's `agent_turn("case:...")` and stream live through the same sink.

Why a ContextVar and not a parameter: `AdapterRequest` is a frozen dataclass
serialized to JSON for the cli/http adapters — a live recorder can't ride in it.
ContextVars propagate to coroutines awaited in the same task *and* across
`asyncio.to_thread`, so both embedded shapes (`async def` and sync callable)
see the handle.

The handle is a deliberately narrow facade: it exposes span emission but *not*
`complete()`/`fail()`/`build()` — trace lifecycle stays the executor's job. When
the agent emits at least one span, `used` flips True and the executor suppresses
the synthetic fallback span (so metrics aren't double-counted).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from selfevals.trace.recorder import (
        TraceRecorder,
        _LLMSpanBuilder,
        _SttSpanBuilder,
        _ToolSpanBuilder,
        _TtsSpanBuilder,
    )


class AgentTraceHandle:
    """Narrow, agent-facing facade over a live `TraceRecorder`.

    Delegates to the recorder's own span context managers so spans an embedded
    agent opens nest under whatever the executor already opened (the case turn).
    Records that it was `used` the first time any span is opened, which the
    executor reads to decide whether to also write the synthetic fallback span.
    """

    def __init__(self, recorder: TraceRecorder) -> None:
        self._recorder = recorder
        self._used = False

    @property
    def used(self) -> bool:
        """True once the agent has emitted at least one span through this handle.

        The executor uses this to suppress the synthetic `adapter_response` span:
        if the agent produced its own LLM/tool spans, the synthetic one would
        double-count tokens/cost (the real spans already accumulated them).
        """
        return self._used

    @contextmanager
    def agent_turn(self, name: str) -> Iterator[None]:
        self._used = True
        with self._recorder.agent_turn(name):
            yield

    @contextmanager
    def llm_call(
        self,
        name: str,
        *,
        provider: str,
        model: str,
        model_version_pinned: str | None = None,
    ) -> Iterator[_LLMSpanBuilder]:
        self._used = True
        with self._recorder.llm_call(
            name,
            provider=provider,
            model=model,
            model_version_pinned=model_version_pinned,
        ) as builder:
            yield builder

    @contextmanager
    def tool_call(
        self,
        name: str,
        *,
        tool_name: str,
        tool_use_id: str | None = None,
        tool_version: str | None = None,
    ) -> Iterator[_ToolSpanBuilder]:
        self._used = True
        with self._recorder.tool_call(
            name,
            tool_name=tool_name,
            tool_use_id=tool_use_id,
            tool_version=tool_version,
        ) as builder:
            yield builder

    @contextmanager
    def stt(self, name: str, *, provider: str) -> Iterator[_SttSpanBuilder]:
        self._used = True
        with self._recorder.stt(name, provider=provider) as builder:
            yield builder

    @contextmanager
    def tts(self, name: str, *, provider: str) -> Iterator[_TtsSpanBuilder]:
        self._used = True
        with self._recorder.tts(name, provider=provider) as builder:
            yield builder

    def add_human_intervention(self, name: str, *, actor: str, action: str) -> None:
        self._used = True
        self._recorder.add_human_intervention(name, actor=actor, action=action)

    def add_guardrail_check(self, name: str, *, guardrail: str, passed: bool) -> None:
        self._used = True
        self._recorder.add_guardrail_check(name, guardrail=guardrail, passed=passed)

    def add_decision(
        self,
        name: str,
        *,
        decision_type: str,
        chosen: str,
        alternatives: list[str] | None = None,
        confidence: float | None = None,
    ) -> None:
        self._used = True
        self._recorder.add_decision(
            name,
            decision_type=decision_type,
            chosen=chosen,
            alternatives=alternatives,
            confidence=confidence,
        )

    def route_payload(self, key: str, value: Any) -> tuple[str | None, str | None, str | None]:
        """Persist a payload the same way the recorder's own writers do.

        Returns `(pointer, hash, inline)` so the agent's shim can set the
        `*_pointer`/`*_hash`/`*_inline` fields on a span builder for messages,
        content, tool args/results, reasoning, etc. Small payloads inline;
        large ones offload to the object store.
        """
        from selfevals.runner.trace_response_writer import route_payload

        return route_payload(self._recorder, key, value)

    def route_audio(self, key: str, data: bytes) -> tuple[str | None, str | None]:
        """Persist audio bytes to the object store, always by pointer.

        Returns `(pointer, hash)`. Unlike `route_payload`, audio is NEVER inlined
        — the inline fields are text, and binary audio always belongs behind a
        pointer regardless of size (a clip is opaque, not a preview). Returns
        `(None, None)` when there's no object store (e.g. `--no-persist`)."""
        router = self._recorder.payload_router
        if router is None:
            return None, None
        routed = router.route_bytes(key, data)
        if routed.pointer is not None:
            return routed.pointer, routed.content_hash
        # Small clip that the router would have inlined: force it to the store
        # anyway so audio is always a resolvable pointer.
        pointer = router.put_bytes(key, data)
        return pointer, routed.content_hash


_current_handle: ContextVar[AgentTraceHandle | None] = ContextVar(
    "selfevals_current_trace_handle", default=None
)


def get_current_trace_handle() -> AgentTraceHandle | None:
    """Return the trace handle bound for the current adapter invocation, if any.

    An embedded agent's selfevals shim calls this to emit real spans. Returns
    None outside a bound `invoke()` (e.g. unit tests, or a cli/http run where no
    in-process recorder exists) — the shim must treat None as "don't trace" and
    stay a no-op, so the agent behaves identically with or without selfevals.
    """
    return _current_handle.get()


@contextmanager
def bind_trace_handle(recorder: TraceRecorder) -> Iterator[AgentTraceHandle]:
    """Bind `recorder` as the ambient trace handle for the enclosed block.

    Used by the executor around `adapter.invoke()`. Restores the previous
    handle on exit (so nested/reentrant runs don't clobber each other).
    """
    handle = AgentTraceHandle(recorder)
    token = _current_handle.set(handle)
    try:
        yield handle
    finally:
        _current_handle.reset(token)
