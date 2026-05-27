"""Executor: run an EvalCase across N repetitions through an AgentAdapter.

The Executor's job is small and load-bearing:
- For each repetition, build a TraceRecorder, invoke the adapter, and
  faithfully record what the adapter saw and returned as spans.
- Mock tool calls per SandboxPolicy.
- Return per-repetition results plus the assembled Traces.

Graders are NOT run here — they read the Traces later (PR 5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from selfeval._internal.ids import new_prefixed_id
from selfeval._internal.time import utc_now
from selfeval.runner.adapters import AdapterError, AdapterRequest
from selfeval.runner.sandbox import SandboxPolicy
from selfeval.schemas.enums import StopReason, ToolCallStatus
from selfeval.schemas.trace import (
    AgentSnapshotRef,
    RunInfo,
    ToolUseRequest,
    Trace,
)
from selfeval.trace.recorder import TraceRecorder

if TYPE_CHECKING:
    from selfeval.runner.adapters import AdapterResponse, AgentAdapter
    from selfeval.schemas.eval_case import EvalCase
    from selfeval.trace.payload_router import PayloadRouter


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


class Executor:
    def __init__(
        self,
        *,
        adapter: AgentAdapter,
        sandbox: SandboxPolicy,
        workspace_id: str,
        framework_version: str = "selfeval/0.0.4",
        runtime: str = "python-3.12",
        payload_router: PayloadRouter | None = None,
    ) -> None:
        if not workspace_id:
            raise ValueError("workspace_id must be non-empty")
        self._adapter = adapter
        self._sandbox = sandbox
        self._workspace_id = workspace_id
        self._framework_version = framework_version
        self._runtime = runtime
        self._payload_router = payload_router
        sandbox.ensure_runnable()

    @property
    def sandbox(self) -> SandboxPolicy:
        return self._sandbox

    def run_case(
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
        results: list[RepetitionResult] = []
        agent_ref = self._agent_ref()
        for rep in range(repetitions):
            run_id = new_prefixed_id("run")
            run_info = RunInfo(
                run_id=run_id,
                experiment_id=experiment_id,
                iteration=iteration,
                variant_id=variant_id,
                eval_case_id=case.id,
                repetition=rep,
            )
            result = self._run_single(
                case=case,
                run_info=run_info,
                agent_ref=agent_ref,
                parameter_overrides=parameter_overrides or {},
            )
            results.append(result)
        return CaseRun(case_id=case.id, repetitions=results)

    def _run_single(
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
                response = self._adapter.invoke(adapter_request)
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
            llm.provider_metadata = dict(response.provider_metadata)
        for tu in response.tool_uses:
            side_effects = False  # placeholder until Tool registry is wired in PR 5/6
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
        # MVP: the case may declare required + forbidden tools. Pass the
        # required set through. Tool registry intersection lands later.
        required = list(case.expected.required_tools)
        if self._sandbox.mode == self._sandbox.mode.MOCK:
            return required
        return required
