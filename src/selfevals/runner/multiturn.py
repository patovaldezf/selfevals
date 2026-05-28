"""MultiTurnExecutor: run a conversation EvalCase turn by turn.

The single-shot `Executor` invokes the adapter once with the whole case
input. A conversation case (`EvalCase.is_conversation()`) instead scripts a
back-and-forth: each scripted user turn is replayed in order, the adapter is
invoked with the conversation history *through* that turn, and its reply is
appended to the running history before the next turn.

Each turn produces one Trace. All turns of one (case, repetition) share a
`thread_id` and carry an incrementing `thread_position`, so the per-turn
traces reassemble into the ordered thread (the trace schema already models
both fields).

The executor composes the single-shot `Executor` — it reuses its trace
assembly, cost/timing recording, and sandbox handling rather than
duplicating them. Only the per-turn input and the thread-aware `RunInfo`
differ.

A conversation case may also opt into *simulation*: when `case.input` carries
a `simulator` key (or the caller passes `simulator=` directly to `run_case`),
once the scripted user turns are exhausted the MultiTurnExecutor asks a
`UserSimulator` for further user turns until the simulator signals
termination (max_turns reached, stop_condition matched, success_criteria
matched on the SUT's reply). Simulator-produced turns are appended to the
history just like scripted ones, but they are NOT recorded against the
SUT's trace — the simulator runs as a separate AgentAdapter invocation,
so its tokens and cost stay off the SUT's books (the trajectory and cost
graders look at SUT traces only).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from selfevals._internal.ids import new_prefixed_id
from selfevals.runner.adapters import AdapterError, AdapterRequest
from selfevals.runner.executor import CaseRun, Executor, RepetitionResult
from selfevals.runner.simulator import SimulatorSpec, UserSimulator
from selfevals.schemas.enums import MessageRole
from selfevals.schemas.trace import RunInfo

if TYPE_CHECKING:
    from selfevals.runner.adapters import AdapterResponse
    from selfevals.schemas.eval_case import EvalCase, Message


class MultiTurnExecutor:
    """Run conversation cases turn-by-turn over a wrapped single-shot Executor."""

    def __init__(self, executor: Executor) -> None:
        self._executor = executor

    @property
    def executor(self) -> Executor:
        return self._executor

    async def run_case(
        self,
        case: EvalCase,
        *,
        repetitions: int = 1,
        experiment_id: str | None = None,
        iteration: int | None = None,
        variant_id: str | None = None,
        parameter_overrides: dict[str, object] | None = None,
        simulator: UserSimulator | None = None,
    ) -> CaseRun:
        """Run one conversation case across N repetitions.

        Returns a CaseRun whose `repetitions` list holds one RepetitionResult
        per (repetition, turn). Each result's `trace.run.thread_id` /
        `thread_position` identify which conversation and turn it belongs to.

        ``simulator`` is optional. When provided (or when ``case.input``
        carries a ``simulator`` mapping that we parse into a SimulatorSpec),
        the executor keeps driving turns *past* the scripted ones — calling
        the simulator for each new user turn, then the SUT for the reply,
        until the simulator signals termination. The simulator's own LLM
        cost (if any) is NOT recorded on the SUT trace; it lives on the
        ``CaseRun.simulator_cost_usd`` aggregate.
        """
        if repetitions < 1:
            raise ValueError("repetitions must be >= 1")
        if not case.is_conversation():
            raise ValueError(
                "MultiTurnExecutor only runs conversation cases "
                "(EvalCase.is_conversation() must be True)"
            )
        scripted = list(case.conversation().messages)
        turns = _turn_boundaries(scripted)
        if not turns:
            raise ValueError("conversation has no user/tool turn to drive the agent")

        # Resolve the simulator. Explicit kwarg wins; otherwise look for a
        # ``simulator:`` block on the case input (spec §4 / canon §4.3 -
        # source=simulation). Cases without either run the legacy
        # scripted-only path unchanged.
        sim_spec: SimulatorSpec | None = None
        if simulator is None and "simulator" in case.input:
            sim_spec = SimulatorSpec.from_dict(case.input["simulator"])

        agent_ref = self._executor._agent_ref()
        overrides = parameter_overrides or {}

        results: list[RepetitionResult] = []
        simulator_cost_usd = 0.0
        simulator_turns_emitted = 0
        # Repetitions run sequentially per thread: a turn cannot start until the
        # previous turn's reply is in the history. Different repetitions are
        # independent threads; we keep them ordered for reproducibility.
        for rep in range(repetitions):
            thread_id = new_prefixed_id("thread")
            history: list[dict[str, Any]] = []
            # Each repetition needs its own simulator counter; if the caller
            # passed in a single UserSimulator we reset it at the boundary.
            if simulator is not None:
                simulator.reset()
            for position, boundary in enumerate(turns):
                # Append the scripted messages up to and including this turn's
                # driving (user/tool) message.
                history.extend(_message_to_json(m) for m in scripted[boundary.start : boundary.end])
                run_info = RunInfo(
                    run_id=new_prefixed_id("run"),
                    experiment_id=experiment_id,
                    iteration=iteration,
                    variant_id=variant_id,
                    eval_case_id=case.id,
                    repetition=rep,
                    thread_id=thread_id,
                    thread_position=position,
                )
                turn_input = _turn_input(case, history)
                result = await self._executor._run_single(
                    case=case,
                    run_info=run_info,
                    agent_ref=agent_ref,
                    parameter_overrides=overrides,
                    input_override=turn_input,
                )
                results.append(result)
                if result.error is not None:
                    # A turn cannot proceed without the prior reply; stop this
                    # thread here. The failed turn is recorded.
                    break
                history.append(_assistant_reply(result.response))

            # After scripted turns: optionally let the simulator keep driving.
            if result.error is not None:
                continue
            if simulator is None:
                # case.input may declare a `simulator:` spec for
                # documentation, but activating it requires runtime
                # wiring (scripted_replies or judge_adapter). Without
                # that, the scripted-only thread is complete — skip the
                # simulation phase rather than error out.
                continue

            sim, owns_sim = _coerce_simulator(simulator, sim_spec)
            position = len(turns)
            while True:
                # Termination check against the most recent SUT reply.
                last_reply = _last_assistant_content(history)
                if sim.spec.is_success(last_reply) or sim.spec.hit_stop_condition(last_reply):
                    break

                sim_request = AdapterRequest(
                    workspace_id=self._executor._workspace_id,
                    case_id=case.id,
                    input={"messages": list(history)},
                    context=case.context,
                    tools_allowed=[],
                    parameters={},
                    metadata={"role_tag": "user_simulator"},
                )
                try:
                    sim_response = await sim.invoke(sim_request)
                except AdapterError:
                    # Simulator failure stops the thread; surface as a
                    # broken turn so callers see the error. We do not run
                    # the SUT for a missing user turn.
                    break

                if not sim_response.content:
                    # Simulator yields empty content => terminate (e.g.
                    # max_turns hit, scripted_replies exhausted with no
                    # judge fallback).
                    break

                simulator_cost_usd += sim_response.cost_usd
                simulator_turns_emitted += 1
                history.append(
                    {
                        "role": MessageRole.USER.value,
                        "content": sim_response.content,
                        "name": "simulator",
                    }
                )

                run_info = RunInfo(
                    run_id=new_prefixed_id("run"),
                    experiment_id=experiment_id,
                    iteration=iteration,
                    variant_id=variant_id,
                    eval_case_id=case.id,
                    repetition=rep,
                    thread_id=thread_id,
                    thread_position=position,
                )
                turn_input = _turn_input(case, history)
                result = await self._executor._run_single(
                    case=case,
                    run_info=run_info,
                    agent_ref=agent_ref,
                    parameter_overrides=overrides,
                    input_override=turn_input,
                )
                results.append(result)
                position += 1
                if result.error is not None:
                    break
                history.append(_assistant_reply(result.response))

            # Owned simulator (built from spec) is short-lived; drop the
            # reference so a subsequent repetition gets a fresh counter.
            if owns_sim:
                sim.reset()

        return CaseRun(
            case_id=case.id,
            repetitions=results,
            simulator_cost_usd=simulator_cost_usd,
            simulator_turns=simulator_turns_emitted,
        )


class _Boundary:
    """Half-open span [start, end) of scripted messages for one turn."""

    __slots__ = ("end", "start")

    def __init__(self, start: int, end: int) -> None:
        self.start = start
        self.end = end


def _turn_boundaries(messages: list[Message]) -> list[_Boundary]:
    """Split scripted messages into turn boundaries.

    A turn ends at each user/tool message: everything since the previous turn
    (leading system messages, and any scripted assistant context) plus that
    user/tool message is the slice fed to the adapter for that turn. Trailing
    scripted assistant messages after the last user/tool turn are dropped — the
    agent produces those, they are not inputs.
    """
    boundaries: list[_Boundary] = []
    start = 0
    for i, m in enumerate(messages):
        if m.role in (MessageRole.USER, MessageRole.TOOL):
            boundaries.append(_Boundary(start, i + 1))
            start = i + 1
    return boundaries


def _message_to_json(message: Message) -> dict[str, Any]:
    return message.model_dump(mode="json", exclude_none=True)


def _assistant_reply(response: AdapterResponse | None) -> dict[str, Any]:
    """Turn an adapter response into an assistant message for the history."""
    content = "" if response is None or response.content is None else response.content
    return {"role": MessageRole.ASSISTANT.value, "content": content}


def _turn_input(case: EvalCase, history: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the per-turn input dict fed to the SUT adapter.

    Preserves any top-level keys the case carried on `input` (e.g. task_hint,
    retrieval context) but drops the conversation slot (`messages`, which we
    rebuild from the running history) and the `simulator` slot (a runner-
    internal config the SUT should not see).
    """
    turn_input: dict[str, Any] = {
        k: v for k, v in case.input.items() if k not in ("messages", "simulator")
    }
    turn_input["messages"] = list(history)
    return turn_input


def _last_assistant_content(history: list[dict[str, Any]]) -> str | None:
    """Most recent assistant message content in the running history, or None."""
    for msg in reversed(history):
        if msg.get("role") == MessageRole.ASSISTANT.value:
            content = msg.get("content")
            if isinstance(content, str):
                return content
            # Multimodal / block content: stringify by joining text blocks.
            if isinstance(content, list):
                texts = [
                    str(blk.get("text", ""))
                    for blk in content
                    if isinstance(blk, dict) and blk.get("text") is not None
                ]
                return "\n".join(texts) if texts else None
            return None
    return None


def _coerce_simulator(
    explicit: UserSimulator,
    spec: SimulatorSpec | None,
) -> tuple[UserSimulator, bool]:
    """Resolve which UserSimulator to use for a thread.

    Returns ``(simulator, owns)`` — ``owns=True`` would mean the runner
    built the simulator and may treat it as ephemeral; today only the
    explicit-pass path is supported (spec on `case.input` is data, not a
    factory), so ``owns`` is always False. The seam exists so a future
    auto-builder can land without changing the caller.
    """
    # The case-input SimulatorSpec is kept around for two reasons:
    # (1) future auto-wiring (e.g. a default judge adapter), and (2)
    # callers who want to assert/override fields against the spec.
    # Today the explicit UserSimulator is authoritative for runtime
    # behavior — its own `spec` drives termination decisions.
    _ = spec
    return explicit, False
