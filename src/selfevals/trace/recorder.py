"""TraceRecorder — capture spans during agent execution and assemble a Trace.

Usage:

    with TraceRecorder(
        workspace_id=ws.id,
        run=RunInfo(run_id="run_001"),
        agent=AgentSnapshotRef(agent_id=ag.id, agent_version=1),
        environment_started_at=utc_now(),
        framework_version="selfevals/0.0.3",
        runtime="python-3.12",
        sandbox=SandboxMode.MOCK,
        payload_router=router,
    ) as rec:
        with rec.agent_turn("turn"):
            with rec.llm_call("model", provider="anthropic", model="...") as llm:
                llm.set_output(stop_reason=StopReason.TOOL_USE, tool_use_requested=[...])
                llm.add_tokens(input=100, output=20, total=120)
            with rec.tool_call("search", tool_name="search", tool_use_id="toolu_01") as tc:
                ...
        rec.complete()
    trace = rec.build()

The recorder is intentionally minimal — it stitches span_started_at +
duration via `time.perf_counter()`, accumulates spans in order, and lets
the caller fill in payload-specific fields via small builders. It is not
threadsafe: one recorder per execution.
"""

from __future__ import annotations

import time
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Self

from selfevals._internal.ids import new_prefixed_id
from selfevals._internal.time import utc_now
from selfevals.schemas.enums import (
    SandboxMode,
    StopReason,
    ToolCallStatus,
    TraceState,
)
from selfevals.schemas.trace import (
    AgentSnapshotRef,
    AgentTurnSpan,
    CostBreakdown,
    EnvironmentInfo,
    FinalState,
    GraderResult,
    LLMCallSpan,
    LLMOutput,
    ReasoningBlock,
    RetrievedDoc,
    RunInfo,
    Span,
    SttSpan,
    TokenBreakdown,
    ToolCallSpan,
    ToolUseRequest,
    Trace,
    TraceLink,
    TraceMetrics,
    TraceOutputs,
    TtsSpan,
)
from selfevals.trace.recorder_spans import (
    build_decision_span,
    build_error_span,
    build_guardrail_check_span,
    build_handoff_span,
    build_human_intervention_span,
    build_memory_read_span,
    build_memory_write_span,
    build_retrieval_span,
)
from selfevals.trace.span_sink import NO_OP_SINK, SpanSink
from selfevals.trace.span_view import span_view

if TYPE_CHECKING:
    from collections.abc import Iterator

    from selfevals.trace.payload_router import PayloadRouter


def _new_span_id() -> str:
    return new_prefixed_id("sp")


@dataclass
class _LLMSpanBuilder:
    """Mutator returned by `recorder.llm_call`; gets baked into a span on exit."""

    provider: str
    model: str
    model_version_pinned: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    reasoning: ReasoningBlock = field(default_factory=ReasoningBlock)
    output: LLMOutput = field(default_factory=LLMOutput)
    tokens: TokenBreakdown = field(default_factory=TokenBreakdown)
    cost: CostBreakdown = field(default_factory=CostBreakdown)
    cache_hit: bool = False
    retries: int = 0
    provider_metadata: dict[str, Any] = field(default_factory=dict)
    system_prompt_pointer: str | None = None
    system_prompt_hash: str | None = None
    system_prompt_inline: str | None = None
    messages_pointer: str | None = None
    messages_hash: str | None = None
    messages_inline: str | None = None
    tools_offered: list[str] = field(default_factory=list)
    tools_offered_hash: str | None = None
    time_to_first_token_ms: int | None = None
    tokens_per_second: float | None = None

    def set_output(
        self,
        *,
        stop_reason: StopReason | None = None,
        content_pointer: str | None = None,
        content_hash: str | None = None,
        content_inline: str | None = None,
        tool_use_requested: list[ToolUseRequest] | None = None,
    ) -> None:
        self.output = LLMOutput(
            stop_reason=stop_reason,
            content_pointer=content_pointer,
            content_hash=content_hash,
            content_inline=content_inline,
            tool_use_requested=tool_use_requested or [],
        )

    def add_tokens(
        self,
        *,
        input: int = 0,
        input_cache_read: int = 0,
        input_cache_creation: int = 0,
        output: int = 0,
        reasoning: int = 0,
        total: int | None = None,
    ) -> None:
        component_sum = input + input_cache_read + input_cache_creation + output + reasoning
        self.tokens = TokenBreakdown(
            input=input,
            input_cache_read=input_cache_read,
            input_cache_creation=input_cache_creation,
            output=output,
            reasoning=reasoning,
            total=total if total is not None else component_sum,
        )

    def set_timing(
        self,
        *,
        time_to_first_token_ms: int | None = None,
        tokens_per_second: float | None = None,
    ) -> None:
        """Record streaming-latency signals for this call.

        `time_to_first_token_ms` is only meaningful when the adapter streamed
        and measured it; leave it None otherwise rather than fabricating one.
        `tokens_per_second` may be measured by the adapter or derived from the
        span duration when the adapter does not report it.
        """
        if time_to_first_token_ms is not None:
            self.time_to_first_token_ms = time_to_first_token_ms
        if tokens_per_second is not None:
            self.tokens_per_second = tokens_per_second

    def set_cost(self, cost: CostBreakdown | None) -> None:
        """Record this call's cost breakdown.

        A None cost leaves the default zero breakdown in place (the model was
        unpriced); the recorder never fabricates a cost.
        """
        if cost is not None:
            self.cost = cost

    def set_reasoning(
        self,
        *,
        summary_pointer: str | None = None,
        full_pointer: str | None = None,
        thinking_tokens: int = 0,
        signature: str | None = None,
        redacted: bool = False,
    ) -> None:
        """Record the model's extended-thinking block for this call.

        Marks `available=True` so the viewer knows reasoning was captured (vs.
        the default empty block, which reads as "not captured"). The thinking
        text itself lives behind `summary_pointer`/`full_pointer` (routed to the
        object store like any other payload); pass `redacted=True` when the
        provider returned a redacted block with no readable text.
        """
        self.reasoning = ReasoningBlock(
            available=True,
            redacted=redacted,
            summary_pointer=summary_pointer,
            full_pointer=full_pointer,
            thinking_tokens=thinking_tokens,
            signature=signature,
        )


@dataclass
class _ToolSpanBuilder:
    tool_name: str
    tool_use_id: str | None = None
    tool_version: str | None = None
    args_pointer: str | None = None
    args_hash: str | None = None
    args_inline: str | None = None
    result_pointer: str | None = None
    result_hash: str | None = None
    result_inline: str | None = None
    status: ToolCallStatus = ToolCallStatus.OK
    error: str | None = None
    retry_chain: list[str] = field(default_factory=list)
    sandboxed: bool = False
    side_effects: dict[str, Any] = field(default_factory=dict)


@dataclass
class _SttSpanBuilder:
    provider: str
    model: str | None = None
    audio_pointer: str | None = None
    audio_hash: str | None = None
    audio_duration_ms: int | None = None
    audio_mime_type: str | None = None
    transcript_pointer: str | None = None
    transcript_hash: str | None = None
    transcript_inline: str | None = None
    language: str | None = None
    confidence: float | None = None
    streaming: bool = False
    time_to_first_transcript_ms: int | None = None
    provider_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class _TtsSpanBuilder:
    provider: str
    model: str | None = None
    voice_id: str | None = None
    text_pointer: str | None = None
    text_hash: str | None = None
    text_inline: str | None = None
    audio_pointer: str | None = None
    audio_hash: str | None = None
    audio_duration_ms: int | None = None
    audio_mime_type: str | None = None
    time_to_first_byte_ms: int | None = None
    provider_metadata: dict[str, Any] = field(default_factory=dict)


def _build_stt_span(
    *, span_id: str, parent_id: str | None, name: str, started_at: datetime,
    duration_ms: int, builder: _SttSpanBuilder,
) -> SttSpan:
    return SttSpan(
        id=span_id, parent_id=parent_id, name=name, started_at=started_at,
        duration_ms=duration_ms, provider=builder.provider, model=builder.model,
        audio_pointer=builder.audio_pointer, audio_hash=builder.audio_hash,
        audio_duration_ms=builder.audio_duration_ms, audio_mime_type=builder.audio_mime_type,
        transcript_pointer=builder.transcript_pointer, transcript_hash=builder.transcript_hash,
        transcript_inline=builder.transcript_inline, language=builder.language,
        confidence=builder.confidence, streaming=builder.streaming,
        time_to_first_transcript_ms=builder.time_to_first_transcript_ms,
        provider_metadata=builder.provider_metadata,
    )


def _build_tts_span(
    *, span_id: str, parent_id: str | None, name: str, started_at: datetime,
    duration_ms: int, builder: _TtsSpanBuilder,
) -> TtsSpan:
    return TtsSpan(
        id=span_id, parent_id=parent_id, name=name, started_at=started_at,
        duration_ms=duration_ms, provider=builder.provider, model=builder.model,
        voice_id=builder.voice_id, text_pointer=builder.text_pointer,
        text_hash=builder.text_hash, text_inline=builder.text_inline,
        audio_pointer=builder.audio_pointer, audio_hash=builder.audio_hash,
        audio_duration_ms=builder.audio_duration_ms, audio_mime_type=builder.audio_mime_type,
        time_to_first_byte_ms=builder.time_to_first_byte_ms,
        provider_metadata=builder.provider_metadata,
    )


def _build_llm_call_span(
    *,
    span_id: str,
    parent_id: str | None,
    name: str,
    started_at: datetime,
    duration_ms: int,
    builder: _LLMSpanBuilder,
) -> LLMCallSpan:
    tokens_per_second = builder.tokens_per_second
    if tokens_per_second is None and builder.tokens.output > 0 and duration_ms > 0:
        # Derive throughput from wall-clock when the adapter did not measure
        # it directly. TTFT stays None unless the adapter streamed and
        # reported it — we never fabricate a TTFT.
        tokens_per_second = builder.tokens.output / (duration_ms / 1000)
    return LLMCallSpan(
        id=span_id,
        parent_id=parent_id,
        name=name,
        started_at=started_at,
        duration_ms=duration_ms,
        provider=builder.provider,
        model=builder.model,
        model_version_pinned=builder.model_version_pinned,
        system_prompt_pointer=builder.system_prompt_pointer,
        system_prompt_hash=builder.system_prompt_hash,
        system_prompt_inline=builder.system_prompt_inline,
        messages_pointer=builder.messages_pointer,
        messages_hash=builder.messages_hash,
        messages_inline=builder.messages_inline,
        tools_offered=builder.tools_offered,
        tools_offered_hash=builder.tools_offered_hash,
        params=builder.params,
        reasoning=builder.reasoning,
        output=builder.output,
        tokens=builder.tokens,
        cost_usd=builder.cost,
        time_to_first_token_ms=builder.time_to_first_token_ms,
        tokens_per_second=tokens_per_second,
        retries=builder.retries,
        cache_hit=builder.cache_hit,
        provider_metadata=builder.provider_metadata,
    )


def _build_tool_call_span(
    *,
    span_id: str,
    parent_id: str | None,
    name: str,
    started_at: datetime,
    duration_ms: int,
    builder: _ToolSpanBuilder,
) -> ToolCallSpan:
    return ToolCallSpan(
        id=span_id,
        parent_id=parent_id,
        name=name,
        started_at=started_at,
        duration_ms=duration_ms,
        tool_name=builder.tool_name,
        tool_version=builder.tool_version,
        tool_use_id=builder.tool_use_id,
        args_pointer=builder.args_pointer,
        args_hash=builder.args_hash,
        args_inline=builder.args_inline,
        result_pointer=builder.result_pointer,
        result_hash=builder.result_hash,
        result_inline=builder.result_inline,
        status=builder.status,
        error=builder.error,
        retry_chain=builder.retry_chain,
        sandboxed=builder.sandboxed,
        side_effects=builder.side_effects,
    )


class TraceRecorder:
    """Build a Trace by capturing span context managers in order."""

    def __init__(
        self,
        *,
        workspace_id: str,
        run: RunInfo,
        agent: AgentSnapshotRef,
        framework_version: str,
        runtime: str,
        sandbox: SandboxMode,
        environment_started_at: datetime | None = None,
        payload_router: PayloadRouter | None = None,
        span_sink: SpanSink | None = None,
    ) -> None:
        if not workspace_id:
            raise ValueError("workspace_id must be non-empty")
        self._workspace_id = workspace_id
        self._run = run
        self._agent = agent
        self._framework_version = framework_version
        self._runtime = runtime
        self._sandbox = sandbox
        self._env_started_at = environment_started_at or utc_now()
        self._env_ended_at: datetime | None = None
        self._payload_router = payload_router
        # Live span fan-out. NO_OP by default so non-serve runs pay nothing;
        # `selfevals serve` injects a broker-backed sink. The sink is called
        # from this (possibly background) thread and must be non-blocking.
        self._span_sink = span_sink or NO_OP_SINK
        self._spans: list[Span] = []
        self._open_parents: list[str] = []
        self._grader_results: list[GraderResult] = []
        self._outputs = TraceOutputs()
        self._links: list[TraceLink] = []
        self._final_state: FinalState | None = None
        self._tokens_in = 0
        self._tokens_out = 0
        self._cost_usd = 0.0
        self._tool_call_count = 0
        self._llm_call_count = 0
        self._retries = 0

    @property
    def workspace_id(self) -> str:
        return self._workspace_id

    @property
    def payload_router(self) -> PayloadRouter | None:
        return self._payload_router

    def __enter__(self) -> Self:
        # Open the live channel up front so the "live" pill lights the moment
        # a run starts — before the first span finishes.
        self._sink_call(
            self._span_sink.on_trace_started, self._workspace_id, self._run.run_id
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> None:
        if self._final_state is None:
            if exc is not None:
                self._final_state = FinalState(status=TraceState.ERRORED, error=str(exc))
            else:
                self._final_state = FinalState(status=TraceState.COMPLETED)
        self._env_ended_at = utc_now()
        self._sink_call(
            self._span_sink.on_trace_finished,
            self._workspace_id,
            self._run.run_id,
            str(self._final_state.status),
        )

    def _record(self, span: Span) -> None:
        """Append a finished span and fan it out to the live sink.

        Every span the recorder produces flows through here so the live
        stream sees exactly what `build()` will persist — in the same
        `SpanSummary` view shape the REST snapshot uses.
        """
        self._spans.append(span)
        self._sink_call(
            self._span_sink.on_span_finished,
            self._workspace_id,
            self._run.run_id,
            span_view(span),
        )

    def _sink_call(self, fn: Any, *args: Any) -> None:
        """Invoke a sink callback, swallowing any error.

        The live stream is best-effort (storage is the source of truth); a
        broken or slow subscriber must never fail or stall a run.
        """
        with suppress(Exception):
            fn(*args)

    def complete(self) -> None:
        self._final_state = FinalState(status=TraceState.COMPLETED)

    def abort(self, reason: str | None = None) -> None:
        self._final_state = FinalState(status=TraceState.ABORTED, error=reason)

    def fail(self, error: str) -> None:
        self._final_state = FinalState(status=TraceState.ERRORED, error=error)

    def timeout(self) -> None:
        self._final_state = FinalState(status=TraceState.TIMEOUT)

    def add_grader_result(self, result: GraderResult) -> None:
        self._grader_results.append(result)

    def set_outputs(self, outputs: TraceOutputs) -> None:
        self._outputs = outputs

    def add_link(self, link: TraceLink) -> None:
        self._links.append(link)

    @contextmanager
    def agent_turn(self, name: str) -> Iterator[None]:
        span_id = _new_span_id()
        started_at = utc_now()
        t0 = time.perf_counter()
        parent = self._current_parent()
        self._open_parents.append(span_id)
        try:
            yield
        finally:
            self._open_parents.pop()
            duration_ms = int((time.perf_counter() - t0) * 1000)
            self._record(
                AgentTurnSpan(
                    id=span_id,
                    parent_id=parent,
                    name=name,
                    started_at=started_at,
                    duration_ms=duration_ms,
                )
            )

    @contextmanager
    def llm_call(
        self,
        name: str,
        *,
        provider: str,
        model: str,
        model_version_pinned: str | None = None,
    ) -> Iterator[_LLMSpanBuilder]:
        span_id = _new_span_id()
        started_at = utc_now()
        t0 = time.perf_counter()
        parent = self._current_parent()
        builder = _LLMSpanBuilder(
            provider=provider,
            model=model,
            model_version_pinned=model_version_pinned,
        )
        self._open_parents.append(span_id)
        try:
            yield builder
        finally:
            self._open_parents.pop()
            duration_ms = int((time.perf_counter() - t0) * 1000)
            span = _build_llm_call_span(
                span_id=span_id,
                parent_id=parent,
                name=name,
                started_at=started_at,
                duration_ms=duration_ms,
                builder=builder,
            )
            self._record(span)
            self._llm_call_count += 1
            self._tokens_in += span.tokens.input + span.tokens.input_cache_read
            self._tokens_out += span.tokens.output
            self._cost_usd += span.cost_usd.total
            self._retries += span.retries

    @contextmanager
    def tool_call(
        self,
        name: str,
        *,
        tool_name: str,
        tool_use_id: str | None = None,
        tool_version: str | None = None,
    ) -> Iterator[_ToolSpanBuilder]:
        span_id = _new_span_id()
        started_at = utc_now()
        t0 = time.perf_counter()
        parent = self._current_parent()
        builder = _ToolSpanBuilder(
            tool_name=tool_name,
            tool_use_id=tool_use_id,
            tool_version=tool_version,
        )
        self._open_parents.append(span_id)
        try:
            yield builder
        except Exception as exc:
            builder.status = ToolCallStatus.ERROR
            builder.error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            self._open_parents.pop()
            duration_ms = int((time.perf_counter() - t0) * 1000)
            self._record(
                _build_tool_call_span(
                    span_id=span_id,
                    parent_id=parent,
                    name=name,
                    started_at=started_at,
                    duration_ms=duration_ms,
                    builder=builder,
                )
            )
            self._tool_call_count += 1

    @contextmanager
    def stt(self, name: str, *, provider: str) -> Iterator[_SttSpanBuilder]:
        """Record a speech-to-text leg. The builder is filled after transcription
        (audio pointer, transcript, latency) and baked into an `SttSpan` on exit."""
        span_id = _new_span_id()
        started_at = utc_now()
        t0 = time.perf_counter()
        parent = self._current_parent()
        builder = _SttSpanBuilder(provider=provider)
        self._open_parents.append(span_id)
        try:
            yield builder
        finally:
            self._open_parents.pop()
            duration_ms = int((time.perf_counter() - t0) * 1000)
            self._record(
                _build_stt_span(
                    span_id=span_id, parent_id=parent, name=name,
                    started_at=started_at, duration_ms=duration_ms, builder=builder,
                )
            )

    @contextmanager
    def tts(self, name: str, *, provider: str) -> Iterator[_TtsSpanBuilder]:
        """Record a text-to-speech leg. The builder is filled after synthesis
        (text, audio pointer, latency) and baked into a `TtsSpan` on exit."""
        span_id = _new_span_id()
        started_at = utc_now()
        t0 = time.perf_counter()
        parent = self._current_parent()
        builder = _TtsSpanBuilder(provider=provider)
        self._open_parents.append(span_id)
        try:
            yield builder
        finally:
            self._open_parents.pop()
            duration_ms = int((time.perf_counter() - t0) * 1000)
            self._record(
                _build_tts_span(
                    span_id=span_id, parent_id=parent, name=name,
                    started_at=started_at, duration_ms=duration_ms, builder=builder,
                )
            )

    def add_retrieval(
        self,
        name: str,
        *,
        retriever: str,
        top_k_requested: int,
        retrieved: list[RetrievedDoc] | None = None,
        reranker: str | None = None,
        query_pointer: str | None = None,
        query_hash: str | None = None,
    ) -> None:
        self._record(
            build_retrieval_span(
                span_id=_new_span_id(),
                parent_id=self._current_parent(),
                name=name,
                started_at=utc_now(),
                retriever=retriever,
                top_k_requested=top_k_requested,
                retrieved=retrieved,
                reranker=reranker,
                query_pointer=query_pointer,
                query_hash=query_hash,
            )
        )

    def add_memory_read(self, name: str, *, store: str, hits: list[str], misses: list[str]) -> None:
        self._record(
            build_memory_read_span(
                span_id=_new_span_id(),
                parent_id=self._current_parent(),
                name=name,
                started_at=utc_now(),
                store=store,
                hits=hits,
                misses=misses,
            )
        )

    def add_memory_write(self, name: str, *, store: str, keys: list[str]) -> None:
        self._record(
            build_memory_write_span(
                span_id=_new_span_id(),
                parent_id=self._current_parent(),
                name=name,
                started_at=utc_now(),
                store=store,
                keys=keys,
            )
        )

    def add_decision(
        self,
        name: str,
        *,
        decision_type: str,
        chosen: str,
        alternatives: list[str] | None = None,
        confidence: float | None = None,
    ) -> None:
        self._record(
            build_decision_span(
                span_id=_new_span_id(),
                parent_id=self._current_parent(),
                name=name,
                started_at=utc_now(),
                decision_type=decision_type,
                chosen=chosen,
                alternatives=alternatives,
                confidence=confidence,
            )
        )

    def add_handoff(self, name: str, *, target: str) -> None:
        self._record(
            build_handoff_span(
                span_id=_new_span_id(),
                parent_id=self._current_parent(),
                name=name,
                started_at=utc_now(),
                target=target,
            )
        )

    def add_human_intervention(self, name: str, *, actor: str, action: str) -> None:
        self._record(
            build_human_intervention_span(
                span_id=_new_span_id(),
                parent_id=self._current_parent(),
                name=name,
                started_at=utc_now(),
                actor=actor,
                action=action,
            )
        )

    def add_guardrail_check(self, name: str, *, guardrail: str, passed: bool) -> None:
        self._record(
            build_guardrail_check_span(
                span_id=_new_span_id(),
                parent_id=self._current_parent(),
                name=name,
                started_at=utc_now(),
                guardrail=guardrail,
                passed=passed,
            )
        )

    def add_error(
        self, name: str, *, error_type: str, message: str, recoverable: bool = False
    ) -> None:
        self._record(
            build_error_span(
                span_id=_new_span_id(),
                parent_id=self._current_parent(),
                name=name,
                started_at=utc_now(),
                error_type=error_type,
                message=message,
                recoverable=recoverable,
            )
        )

    def build(self) -> Trace:
        if self._final_state is None:
            self.complete()
        env = EnvironmentInfo(
            framework_version=self._framework_version,
            runtime=self._runtime,
            sandbox=self._sandbox,
            started_at=self._env_started_at,
            ended_at=self._env_ended_at or utc_now(),
        )
        duration_ms = 0
        if env.ended_at is not None:
            delta: timedelta = env.ended_at - env.started_at
            duration_ms = int(delta.total_seconds() * 1000)
        metrics = TraceMetrics(
            total_tokens_in=self._tokens_in,
            total_tokens_out=self._tokens_out,
            total_cost_usd=self._cost_usd,
            total_duration_ms=duration_ms,
            tool_call_count=self._tool_call_count,
            llm_call_count=self._llm_call_count,
            retries=self._retries,
        )
        final = self._final_state or FinalState(status=TraceState.COMPLETED)
        return Trace(
            id=Trace.make_id(),
            workspace_id=self._workspace_id,
            run=self._run,
            agent=self._agent,
            environment=env,
            final_state=final,
            spans=list(self._spans),
            outputs=self._outputs,
            grader_results=list(self._grader_results),
            metrics=metrics,
            links=list(self._links),
        )

    def _current_parent(self) -> str | None:
        return self._open_parents[-1] if self._open_parents else None

    @property
    def span_count(self) -> int:
        return len(self._spans)
