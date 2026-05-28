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
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from selfevals._internal.ids import new_prefixed_id
from selfevals.runner.executor import CaseRun, Executor, RepetitionResult
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
    ) -> CaseRun:
        """Run one conversation case across N repetitions.

        Returns a CaseRun whose `repetitions` list holds one RepetitionResult
        per (repetition, turn). Each result's `trace.run.thread_id` /
        `thread_position` identify which conversation and turn it belongs to.
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

        agent_ref = self._executor._agent_ref()
        overrides = parameter_overrides or {}

        results: list[RepetitionResult] = []
        # Repetitions run sequentially per thread: a turn cannot start until the
        # previous turn's reply is in the history. Different repetitions are
        # independent threads; we keep them ordered for reproducibility.
        for rep in range(repetitions):
            thread_id = new_prefixed_id("thread")
            history: list[dict[str, Any]] = []
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
                # Preserve any top-level keys the case carried on `input`
                # (e.g. task_hint, retrieval context) by only overriding the
                # `messages` slot. Adapters read those keys verbatim.
                turn_input: dict[str, Any] = {
                    k: v for k, v in case.input.items() if k != "messages"
                }
                turn_input["messages"] = list(history)
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

        return CaseRun(case_id=case.id, repetitions=results)


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
