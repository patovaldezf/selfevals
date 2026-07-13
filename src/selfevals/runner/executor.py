"""Executor: run an EvalCase across N repetitions through an AgentAdapter.

The Executor's job is small and load-bearing:
- For each repetition, build a TraceRecorder, invoke the adapter, and
  faithfully record what the adapter saw and returned as spans.
- Mock tool calls per SandboxPolicy.
- Return per-repetition results plus the assembled Traces.

Repetitions run concurrently (bounded by a semaphore); graders run
afterward over the assembled Traces, not here.

Writing an `AdapterResponse` to a trace (the LLM call span, payload
routing, cost resolution, tool spans) is split into `trace_response_writer.py`
(same free-function-delegate pattern as
`storage/postgres/mappers/trace_spans_write.py`).
"""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from selfevals._internal.ids import new_prefixed_id
from selfevals._internal.time import utc_now
from selfevals.runner.adapters import AdapterError, AdapterRequest
from selfevals.runner.sandbox import SandboxPolicy
from selfevals.runner.trace_response_writer import record_adapter_response
from selfevals.schemas.trace import AgentSnapshotRef, RunInfo, Trace
from selfevals.trace.context import bind_trace_handle
from selfevals.trace.recorder import TraceRecorder
from selfevals.trace.span_sink import NO_OP_SINK

if TYPE_CHECKING:
    from selfevals.runner.adapters import AdapterResponse, AgentAdapter
    from selfevals.runner.otlp_receiver import ReceiverHandle
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
        otlp_handle: ReceiverHandle | None = None,
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
        # Embedded OTLP receiver (optional). When present, out-of-process agents
        # export their own spans here and we bind it to each rep's recorder so
        # they nest under the case trace. None → only the synthetic
        # `adapter_response` span is recorded (legacy behaviour).
        self._otlp_handle = otlp_handle
        sandbox.ensure_runnable()

    @property
    def sandbox(self) -> SandboxPolicy:
        return self._sandbox

    @property
    def workspace_id(self) -> str:
        return self._workspace_id

    def close(self) -> None:
        """Stop the embedded OTLP receiver, if one was started. Idempotent —
        safe to call from the loop's `finally` even when no receiver exists."""
        if self._otlp_handle is not None:
            self._otlp_handle.stop()
            self._otlp_handle = None

    async def aclose(self) -> None:
        """Async teardown: stop the receiver and release adapter resources.

        The Redis-backed rate limiter holds an async client that must be closed
        from inside the event loop (the loop's `finally` is async), or
        `filterwarnings=error` trips on a leaked connection. The in-process
        adapter has no `aclose`, so this is a no-op there. Idempotent."""
        self.close()
        adapter_close = getattr(self._adapter, "aclose", None)
        if callable(adapter_close):
            await adapter_close()

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
        agent_ref = self.agent_ref()
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
                return await self.run_single(
                    case=case,
                    run_info=run_info,
                    agent_ref=agent_ref,
                    parameter_overrides=overrides,
                )

        # gather preserves input order, so results stay ordered by rep index.
        # No return_exceptions: a non-AdapterError propagates (AdapterError is
        # caught inside run_single and recorded as RepetitionResult.error).
        results = await asyncio.gather(*(_bounded(rep) for rep in range(repetitions)))
        return CaseRun(case_id=case.id, repetitions=list(results))

    async def run_single(
        self,
        *,
        case: EvalCase,
        run_info: RunInfo,
        agent_ref: AgentSnapshotRef,
        parameter_overrides: dict[str, object],
        input_override: dict[str, object] | None = None,
    ) -> RepetitionResult:
        """Run one adapter invocation and assemble its Trace.

        Public: `MultiTurnExecutor` composes `Executor` and calls this
        directly, once per conversation turn, so it reuses trace assembly,
        cost/timing recording, and sandbox handling instead of duplicating
        them. `input_override` lets that caller feed a turn-specific
        conversation history instead of the case's raw input; when None the
        case input is used verbatim (the single-shot path).
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
            otlp_endpoint=(self._otlp_handle.endpoint if self._otlp_handle is not None else None),
        )

        # Bind the OTLP receiver to this rep's recorder so spans the agent
        # exports during invoke() (its LLM calls, chains) drain into this
        # case's trace. nullcontext when no receiver is running.
        otlp_ctx = (
            self._otlp_handle.bind_recorder(recorder)
            if self._otlp_handle is not None
            else nullcontext()
        )

        error: str | None = None
        response: AdapterResponse | None = None
        # Bind the recorder as the ambient trace handle so an in-process
        # (embedded) agent can emit its own real spans (LLM calls, tool calls,
        # reasoning) instead of the reconstructed synthetic one. The handle
        # opens inside the case turn so its spans nest under it.
        with (
            otlp_ctx,
            recorder,
            recorder.agent_turn(f"case:{case.name}"),
            bind_trace_handle(recorder) as trace_handle,
        ):
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
                # If the agent emitted its own spans, skip the synthetic
                # `adapter_response` span — it would double-count tokens/cost
                # the real spans already recorded. Structured output is still
                # captured either way.
                record_adapter_response(
                    recorder,
                    response,
                    adapter_request,
                    adapter=self._adapter,
                    sandbox=self._sandbox,
                    agent_emitted_spans=trace_handle.used,
                )
        # Recorder __exit__ marks state based on exception flow; if we
        # already called `recorder.fail()` above, that wins.
        trace = recorder.build()
        return RepetitionResult(
            repetition=run_info.repetition,
            trace=trace,
            response=response,
            error=error,
        )

    def agent_ref(self) -> AgentSnapshotRef:
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
