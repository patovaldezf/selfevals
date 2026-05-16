"""TraceRecorder — capture spans during agent execution and assemble a Trace.

Usage:

    with TraceRecorder(
        workspace_id=ws.id,
        run=RunInfo(run_id="run_001"),
        agent=AgentSnapshotRef(agent_id=ag.id, agent_version=1),
        environment_started_at=utc_now(),
        framework_version="bootstrap/0.0.3",
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
the caller fill in payload-specific fields via small builders. No threading
in MVP.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Self

from bootstrap._internal.ids import new_prefixed_id
from bootstrap._internal.time import utc_now
from bootstrap.schemas.enums import (
    SandboxMode,
    StopReason,
    ToolCallStatus,
    TraceState,
)
from bootstrap.schemas.trace import (
    AgentSnapshotRef,
    AgentTurnSpan,
    DecisionSpan,
    EnvironmentInfo,
    ErrorSpan,
    FinalState,
    GraderResult,
    GuardrailCheckSpan,
    HandoffSpan,
    HumanInterventionSpan,
    LLMCallSpan,
    LLMOutput,
    MemoryReadSpan,
    MemoryWriteSpan,
    ReasoningBlock,
    RetrievalSpan,
    RetrievedDoc,
    RunInfo,
    Span,
    TokenBreakdown,
    ToolCallSpan,
    ToolUseRequest,
    Trace,
    TraceLink,
    TraceMetrics,
    TraceOutputs,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from bootstrap.trace.payload_router import PayloadRouter


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
    cache_hit: bool = False
    retries: int = 0
    provider_metadata: dict[str, Any] = field(default_factory=dict)
    system_prompt_pointer: str | None = None
    system_prompt_hash: str | None = None
    messages_pointer: str | None = None
    messages_hash: str | None = None
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
        tool_use_requested: list[ToolUseRequest] | None = None,
    ) -> None:
        self.output = LLMOutput(
            stop_reason=stop_reason,
            content_pointer=content_pointer,
            content_hash=content_hash,
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


@dataclass
class _ToolSpanBuilder:
    tool_name: str
    tool_use_id: str | None = None
    tool_version: str | None = None
    args_pointer: str | None = None
    args_hash: str | None = None
    result_pointer: str | None = None
    result_hash: str | None = None
    status: ToolCallStatus = ToolCallStatus.OK
    error: str | None = None
    retry_chain: list[str] = field(default_factory=list)
    sandboxed: bool = False
    side_effects: dict[str, Any] = field(default_factory=dict)


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

    # --- public API ---

    @property
    def workspace_id(self) -> str:
        return self._workspace_id

    @property
    def payload_router(self) -> PayloadRouter | None:
        return self._payload_router

    def __enter__(self) -> Self:
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

    # --- finishing ---

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

    # --- span context managers ---

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
            self._spans.append(
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
            span = LLMCallSpan(
                id=span_id,
                parent_id=parent,
                name=name,
                started_at=started_at,
                duration_ms=duration_ms,
                provider=builder.provider,
                model=builder.model,
                model_version_pinned=builder.model_version_pinned,
                system_prompt_pointer=builder.system_prompt_pointer,
                system_prompt_hash=builder.system_prompt_hash,
                messages_pointer=builder.messages_pointer,
                messages_hash=builder.messages_hash,
                tools_offered=builder.tools_offered,
                tools_offered_hash=builder.tools_offered_hash,
                params=builder.params,
                reasoning=builder.reasoning,
                output=builder.output,
                tokens=builder.tokens,
                time_to_first_token_ms=builder.time_to_first_token_ms,
                tokens_per_second=builder.tokens_per_second,
                retries=builder.retries,
                cache_hit=builder.cache_hit,
                provider_metadata=builder.provider_metadata,
            )
            self._spans.append(span)
            self._llm_call_count += 1
            self._tokens_in += span.tokens.input + span.tokens.input_cache_read
            self._tokens_out += span.tokens.output
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
            self._spans.append(
                ToolCallSpan(
                    id=span_id,
                    parent_id=parent,
                    name=name,
                    started_at=started_at,
                    duration_ms=duration_ms,
                    tool_name=builder.tool_name,
                    tool_version=builder.tool_version,
                    tool_use_id=builder.tool_use_id,
                    args_pointer=builder.args_pointer,
                    args_hash=builder.args_hash,
                    result_pointer=builder.result_pointer,
                    result_hash=builder.result_hash,
                    status=builder.status,
                    error=builder.error,
                    retry_chain=builder.retry_chain,
                    sandboxed=builder.sandboxed,
                    side_effects=builder.side_effects,
                )
            )
            self._tool_call_count += 1

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
        retrieved = retrieved or []
        self._spans.append(
            RetrievalSpan(
                id=_new_span_id(),
                parent_id=self._current_parent(),
                name=name,
                started_at=utc_now(),
                retriever=retriever,
                top_k_requested=top_k_requested,
                top_k_returned=len(retrieved),
                retrieved=retrieved,
                reranker=reranker,
                query_pointer=query_pointer,
                query_hash=query_hash,
            )
        )

    def add_memory_read(self, name: str, *, store: str, hits: list[str], misses: list[str]) -> None:
        self._spans.append(
            MemoryReadSpan(
                id=_new_span_id(),
                parent_id=self._current_parent(),
                name=name,
                started_at=utc_now(),
                memory_store=store,
                keys_hit=hits,
                keys_missed=misses,
            )
        )

    def add_memory_write(self, name: str, *, store: str, keys: list[str]) -> None:
        self._spans.append(
            MemoryWriteSpan(
                id=_new_span_id(),
                parent_id=self._current_parent(),
                name=name,
                started_at=utc_now(),
                memory_store=store,
                keys_written=keys,
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
        self._spans.append(
            DecisionSpan(
                id=_new_span_id(),
                parent_id=self._current_parent(),
                name=name,
                started_at=utc_now(),
                decision_type=decision_type,
                chosen=chosen,
                alternatives_considered=alternatives or [],
                confidence=confidence,
            )
        )

    def add_handoff(self, name: str, *, target: str) -> None:
        self._spans.append(
            HandoffSpan(
                id=_new_span_id(),
                parent_id=self._current_parent(),
                name=name,
                started_at=utc_now(),
                target=target,
            )
        )

    def add_human_intervention(self, name: str, *, actor: str, action: str) -> None:
        self._spans.append(
            HumanInterventionSpan(
                id=_new_span_id(),
                parent_id=self._current_parent(),
                name=name,
                started_at=utc_now(),
                actor=actor,
                action=action,
            )
        )

    def add_guardrail_check(self, name: str, *, guardrail: str, passed: bool) -> None:
        self._spans.append(
            GuardrailCheckSpan(
                id=_new_span_id(),
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
        self._spans.append(
            ErrorSpan(
                id=_new_span_id(),
                parent_id=self._current_parent(),
                name=name,
                started_at=utc_now(),
                error_type=error_type,
                message=message,
                recoverable=recoverable,
            )
        )

    # --- build ---

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

    # --- helpers ---

    def _current_parent(self) -> str | None:
        return self._open_parents[-1] if self._open_parents else None

    @property
    def span_count(self) -> int:
        return len(self._spans)
