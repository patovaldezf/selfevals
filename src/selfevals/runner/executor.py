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
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from selfevals._internal.ids import new_prefixed_id
from selfevals._internal.time import utc_now
from selfevals.runner.adapters import AdapterError, AdapterRequest
from selfevals.runner.sandbox import SandboxPolicy
from selfevals.schemas.enums import StopReason, ToolCallStatus
from selfevals.schemas.trace import (
    AgentSnapshotRef,
    RunInfo,
    ToolUseRequest,
    Trace,
)
from selfevals.trace.recorder import TraceRecorder

if TYPE_CHECKING:
    from selfevals.runner.adapters import AdapterResponse, AgentAdapter
    from selfevals.schemas.eval_case import EvalCase
    from selfevals.trace.payload_router import PayloadRouter


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
    ) -> RepetitionResult:
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
        )

        adapter_request = AdapterRequest(
            workspace_id=self._workspace_id,
            case_id=case.id,
            input=case.input,
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
                self._record_response(recorder, response)
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
    ) -> None:
        with recorder.llm_call(
            "adapter_response",
            provider=(self._adapter.agent.model.provider if self._adapter.agent else "unknown"),
            model=(self._adapter.agent.model.name if self._adapter.agent else "unknown"),
        ) as llm:
            llm.add_tokens(
                input=response.tokens_input,
                input_cache_read=response.tokens_cache_read,
                input_cache_creation=response.tokens_cache_creation,
                output=response.tokens_output,
                reasoning=response.tokens_reasoning,
            )
            tool_use_requests = [
                ToolUseRequest(tool=tu.tool, tool_use_id=tu.tool_use_id)
                for tu in response.tool_uses
            ]
            llm.set_output(
                stop_reason=_normalize_stop_reason(response.stop_reason),
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
            llm.provider_metadata = dict(response.provider_metadata)
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
