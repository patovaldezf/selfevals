"""Executor: run an EvalCase across N repetitions through an AgentAdapter.

The Executor's job is small and load-bearing:
- For each repetition, build a TraceRecorder, invoke the adapter, and
  faithfully record what the adapter saw and returned as spans.
- Mock tool calls per SandboxPolicy.
- Return per-repetition results plus the assembled Traces.

Repetitions run concurrently (bounded by a semaphore); graders run
afterward over the assembled Traces, not here.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from selfevals._internal.ids import new_prefixed_id
from selfevals._internal.time import utc_now
from selfevals.runner.adapters import AdapterError, AdapterRequest
from selfevals.runner.pricing import estimate_cost
from selfevals.runner.sandbox import SandboxPolicy
from selfevals.schemas.enums import StopReason, ToolCallStatus
from selfevals.schemas.trace import (
    INLINE_PAYLOAD_MAX_CHARS,
    AgentSnapshotRef,
    CostBreakdown,
    RunInfo,
    TokenBreakdown,
    ToolUseRequest,
    Trace,
    TraceOutputs,
)
from selfevals.trace.recorder import TraceRecorder
from selfevals.trace.span_sink import NO_OP_SINK

if TYPE_CHECKING:
    from selfevals.runner.adapters import AdapterResponse, AgentAdapter
    from selfevals.schemas.eval_case import EvalCase
    from selfevals.trace.payload_router import PayloadRouter
    from selfevals.trace.span_sink import SpanSink


@dataclass(frozen=True)
class RepetitionResult:
    repetition: int
    trace: Trace
    response: AdapterResponse | None
    error: str | None


@dataclass(frozen=True)
class CaseRun:
    case_id: str
    repetitions: list[RepetitionResult] = field(default_factory=list)
    simulator_cost_usd: float = 0.0
    """Aggregate cost (USD) of UserSimulator turns produced by the
    MultiTurnExecutor for this case run. Stays separate from the SUT's
    trace metrics so trajectory/cost graders that read the SUT's trace are
    unaffected by simulation overhead. Always 0.0 for single-shot Executor
    runs and for conversation cases without a simulator."""

    simulator_turns: int = 0
    """Number of user turns the simulator emitted across all repetitions
    of this case (does not include the scripted user turns)."""

    @property
    def successful_count(self) -> int:
        return sum(1 for r in self.repetitions if r.error is None)

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.repetitions if r.error is not None)


_STOP_REASON_NORMALIZE: dict[str, StopReason] = {sr.value: sr for sr in StopReason}


def _normalize_stop_reason(value: str | None) -> StopReason | None:
    if value is None:
        return None
    return _STOP_REASON_NORMALIZE.get(value.strip().lower())


def _optional_int(value: object) -> int | None:
    """Coerce a provider-metadata timing value to a non-negative int, or None.

    Adapters put streaming timings into `provider_metadata` as plain JSON; be
    forgiving about numeric types but reject anything that is not a usable
    non-negative number.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return int(value) if value >= 0 else None
    return None


def _optional_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value) if value >= 0 else None
    return None


def _str_or_none(value: object) -> str | None:
    """A non-empty string, or None. Used to read provider/model from the
    adapter's free-form `provider_metadata` without trusting its types."""
    if isinstance(value, str) and value.strip():
        return value
    return None


def _extract_system_prompt(request: AdapterRequest) -> str | None:
    """Pull a system prompt out of the proposer envelope, if one is there.

    The editable contract routes prompt overrides through
    `parameters["model_params"]`; a proposer that swaps the system prompt puts it
    under a `system_prompt` (or `system`) key. None when the run carries no
    explicit system prompt — we never fabricate one."""
    inner = (request.parameters or {}).get("model_params") or {}
    for key in ("system_prompt", "system"):
        candidate = inner.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    return None


class Executor:
    def __init__(
        self,
        *,
        adapter: AgentAdapter,
        sandbox: SandboxPolicy,
        workspace_id: str,
        framework_version: str = "selfevals/0.0.4",
        runtime: str = "python-3.12",
        payload_router: PayloadRouter | None = None,
        concurrency: int = 8,
        span_sink: SpanSink | None = None,
    ) -> None:
        if not workspace_id:
            raise ValueError("workspace_id must be non-empty")
        if concurrency < 1:
            raise ValueError("concurrency must be >= 1")
        self._adapter = adapter
        self._sandbox = sandbox
        self._workspace_id = workspace_id
        self._framework_version = framework_version
        self._runtime = runtime
        self._payload_router = payload_router
        self._concurrency = concurrency
        # Live span fan-out, threaded through to every per-rep TraceRecorder.
        # NO_OP by default (CLI runs); `selfevals serve` injects a broker sink.
        self._span_sink = span_sink or NO_OP_SINK
        sandbox.ensure_runnable()

    @property
    def sandbox(self) -> SandboxPolicy:
        return self._sandbox

    async def run_case(
        self,
        case: EvalCase,
        *,
        repetitions: int = 1,
        experiment_id: str | None = None,
        iteration: int | None = None,
        variant_id: str | None = None,
        parameter_overrides: dict[str, object] | None = None,
    ) -> CaseRun:
        if repetitions < 1:
            raise ValueError("repetitions must be >= 1")
        agent_ref = self._agent_ref()
        overrides = parameter_overrides or {}
        sem = asyncio.Semaphore(self._concurrency)

        async def _bounded(rep: int) -> RepetitionResult:
            run_info = RunInfo(
                run_id=new_prefixed_id("run"),
                experiment_id=experiment_id,
                iteration=iteration,
                variant_id=variant_id,
                eval_case_id=case.id,
                repetition=rep,
            )
            async with sem:
                return await self._run_single(
                    case=case,
                    run_info=run_info,
                    agent_ref=agent_ref,
                    parameter_overrides=overrides,
                )

        # gather preserves input order, so results stay ordered by rep index.
        # No return_exceptions: a non-AdapterError propagates (AdapterError is
        # caught inside _run_single and recorded as RepetitionResult.error).
        results = await asyncio.gather(*(_bounded(rep) for rep in range(repetitions)))
        return CaseRun(case_id=case.id, repetitions=list(results))

    async def _run_single(
        self,
        *,
        case: EvalCase,
        run_info: RunInfo,
        agent_ref: AgentSnapshotRef,
        parameter_overrides: dict[str, object],
        input_override: dict[str, object] | None = None,
    ) -> RepetitionResult:
        """Run one adapter invocation and assemble its Trace.

        `input_override` lets a caller (the MultiTurnExecutor) feed a
        turn-specific conversation history instead of the case's raw input;
        when None the case input is used verbatim (the single-shot path).
        """
        started_at = utc_now()
        recorder = TraceRecorder(
            workspace_id=self._workspace_id,
            run=run_info,
            agent=agent_ref,
            framework_version=self._framework_version,
            runtime=self._runtime,
            sandbox=self._sandbox.mode,
            environment_started_at=started_at,
            payload_router=self._payload_router,
            span_sink=self._span_sink,
        )

        adapter_request = AdapterRequest(
            workspace_id=self._workspace_id,
            case_id=case.id,
            input=case.input if input_override is None else input_override,
            context=case.context,
            tools_allowed=self._tools_allowed(case),
            parameters=parameter_overrides,
            metadata={"taxonomy": case.taxonomy.model_dump(mode="json")},
        )

        error: str | None = None
        response: AdapterResponse | None = None
        with recorder, recorder.agent_turn(f"case:{case.name}"):
            try:
                response = await self._adapter.invoke(adapter_request)
            except AdapterError as exc:
                error = str(exc)
                recorder.add_error(
                    "adapter_error",
                    error_type=type(exc).__name__,
                    message=str(exc),
                )
                recorder.fail(str(exc))
            else:
                self._record_response(recorder, response, adapter_request)
        # Recorder __exit__ marks state based on exception flow; if we
        # already called `recorder.fail()` above, that wins.
        trace = recorder.build()
        return RepetitionResult(
            repetition=run_info.repetition,
            trace=trace,
            response=response,
            error=error,
        )

    def _record_response(
        self,
        recorder: TraceRecorder,
        response: AdapterResponse,
        request: AdapterRequest,
    ) -> None:
        # The model name reported by the agent wins (embedded specs declare no
        # model — the function does), then the agent record (cli/http), then
        # "unknown". This is what stops the trace viewer showing model="unknown".
        meta = response.provider_metadata
        provider = _str_or_none(meta.get("provider")) or (
            self._adapter.agent.model.provider if self._adapter.agent else None
        )
        model = _str_or_none(meta.get("model")) or (
            self._adapter.agent.model.name if self._adapter.agent else None
        )
        with recorder.llm_call(
            "adapter_response",
            provider=provider or "unknown",
            model=model or "unknown",
        ) as llm:
            llm.add_tokens(
                input=response.tokens_input,
                input_cache_read=response.tokens_cache_read,
                input_cache_creation=response.tokens_cache_creation,
                output=response.tokens_output,
                reasoning=response.tokens_reasoning,
            )
            # Capture the prompt side: the input messages and the system prompt
            # (when the proposer/case supplies one) so the trace shows what the
            # model was actually asked, not just what it answered.
            messages_ptr, messages_hash, messages_inline = self._route_payload(
                recorder, "messages", request.input
            )
            llm.messages_pointer = messages_ptr
            llm.messages_hash = messages_hash
            llm.messages_inline = messages_inline
            system_prompt = _extract_system_prompt(request)
            if system_prompt is not None:
                sys_ptr, sys_hash, sys_inline = self._route_payload(
                    recorder, "system_prompt", system_prompt
                )
                llm.system_prompt_pointer = sys_ptr
                llm.system_prompt_hash = sys_hash
                llm.system_prompt_inline = sys_inline
            llm.tools_offered = list(request.tools_allowed)
            # Capture the response side: the answer text, inlined when small and
            # offloaded to the object store when large.
            content_ptr, content_hash, content_inline = self._route_payload(
                recorder, "content", response.content
            )
            tool_use_requests = [
                ToolUseRequest(tool=tu.tool, tool_use_id=tu.tool_use_id)
                for tu in response.tool_uses
            ]
            llm.set_output(
                stop_reason=_normalize_stop_reason(response.stop_reason),
                content_pointer=content_ptr,
                content_hash=content_hash,
                content_inline=content_inline,
                tool_use_requested=tool_use_requests,
            )
            llm.set_timing(
                time_to_first_token_ms=_optional_int(
                    response.provider_metadata.get("time_to_first_token_ms")
                ),
                tokens_per_second=_optional_float(
                    response.provider_metadata.get("tokens_per_second")
                ),
            )
            llm.set_cost(self._cost_for(response))
            llm.provider_metadata = dict(response.provider_metadata)
        # Surface the structured output on the trace so the FE can show the
        # detected structured payload alongside the text answer.
        if response.structured_output is not None:
            recorder.set_outputs(TraceOutputs(structured_output=response.structured_output))
        for tu in response.tool_uses:
            # The Tool registry does not yet annotate side-effects, so we treat
            # every tool as side-effect-free for sandbox-mocking decisions.
            side_effects = False
            sandboxed = self._sandbox.should_mock_tool(side_effects=side_effects)
            with recorder.tool_call(
                tu.tool,
                tool_name=tu.tool,
                tool_use_id=tu.tool_use_id,
            ) as tool_span:
                tool_span.sandboxed = sandboxed
                tool_span.status = ToolCallStatus.OK
                # Record what the tool was called with, so the trace shows the
                # tool args, not just that a tool fired.
                if tu.args:
                    args_ptr, args_hash, _inline = self._route_payload(
                        recorder, f"tool_args:{tu.tool}", tu.args
                    )
                    tool_span.args_pointer = args_ptr
                    tool_span.args_hash = args_hash

    def _route_payload(
        self, recorder: TraceRecorder, key: str, value: Any
    ) -> tuple[str | None, str | None, str | None]:
        """Decide how to persist one trace payload: pointer, inline, or both.

        Returns `(pointer, hash, inline)`. Small payloads are inlined on the span
        (so the viewer needs no extra fetch); large ones are offloaded to the
        object store via the recorder's `PayloadRouter` and referenced by
        pointer. Without a router (e.g. `--no-persist`) we only inline, truncated
        to `INLINE_PAYLOAD_MAX_CHARS` so a chatty run can't bloat the trace. A
        None/empty value routes to all-None (honest: nothing to show)."""
        if value is None:
            return None, None, None
        text = value if isinstance(value, str) else json.dumps(value, default=str)
        inline = text if len(text) <= INLINE_PAYLOAD_MAX_CHARS else text[:INLINE_PAYLOAD_MAX_CHARS]
        router = recorder.payload_router
        if router is None:
            return None, None, inline
        routed = router.route_value(key, value)
        # Offloaded → pointer + hash, keep the inline preview too. Inlined by the
        # router (small) → no pointer; the inline text already carries it.
        if routed.pointer is not None:
            return routed.pointer, routed.content_hash, inline
        return None, routed.content_hash, inline

    def _cost_for(self, response: AdapterResponse) -> CostBreakdown | None:
        """Resolve the cost of one adapter response.

        The adapter's `cost_usd` is authoritative when it reports one — we keep
        it as the breakdown total (the component split is the provider's, not
        ours to infer). When the adapter reports no cost, derive it from the
        response tokens and the agent's model via the pricing table. An unknown
        model yields None (a one-time warning fires in the pricing layer) — we
        never fabricate a cost.
        """
        if response.cost_usd > 0:
            return CostBreakdown(total=response.cost_usd)
        agent = self._adapter.agent
        if agent is None:
            return None
        tokens = TokenBreakdown(
            input=response.tokens_input,
            input_cache_read=response.tokens_cache_read,
            input_cache_creation=response.tokens_cache_creation,
            output=response.tokens_output,
            reasoning=response.tokens_reasoning,
            total=(
                response.tokens_input
                + response.tokens_cache_read
                + response.tokens_cache_creation
                + response.tokens_output
                + response.tokens_reasoning
            ),
        )
        return estimate_cost(agent.model.provider, agent.model.name, tokens)

    def _agent_ref(self) -> AgentSnapshotRef:
        ag = self._adapter.agent
        if ag is None:
            return AgentSnapshotRef(agent_id="unknown", agent_version=1)
        return AgentSnapshotRef(
            agent_id=ag.id,
            agent_version=ag.version,
            fleet_version=None,
            parameters_snapshot_id=None,
        )

    def _tools_allowed(self, case: EvalCase) -> list[str]:
        # The case may declare required + forbidden tools; pass the required
        # set through. Intersecting against a Tool registry is not wired yet.
        required = list(case.expected.required_tools)
        if self._sandbox.mode == self._sandbox.mode.MOCK:
            return required
        return required
