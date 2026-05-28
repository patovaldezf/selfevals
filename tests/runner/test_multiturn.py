from __future__ import annotations

from typing import Any

import pytest

from selfevals.runner.adapters import AdapterError, AdapterRequest, AdapterResponse, EmbeddedAdapter
from selfevals.runner.executor import Executor
from selfevals.runner.multiturn import MultiTurnExecutor
from selfevals.runner.sandbox import SandboxPolicy
from selfevals.schemas.enums import (
    AgentType,
    DatasetSource,
    DatasetType,
    GroundTruthMethod,
    Level,
    SandboxMode,
)
from selfevals.schemas.eval_case import (
    CaseTaxonomy,
    EvalCase,
    Expected,
    FeatureTag,
    GroundTruthSpec,
    SourceInfo,
)
from selfevals.schemas.fleet import Agent, ModelRef

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _agent() -> Agent:
    return Agent(
        id=Agent.make_id(),
        workspace_id=WS,
        agent_type=AgentType.SYSTEM_PROMPT,
        model=ModelRef(provider="anthropic", name="claude-sonnet-4-6"),
        system_prompt_pointer="oss://prompts/echo",
    )


def _conversation_case(messages: list[dict[str, Any]]) -> EvalCase:
    return EvalCase(
        id=EvalCase.make_id(),
        workspace_id=WS,
        name="chat",
        task_type="smoke",
        input={"messages": messages},
        taxonomy=CaseTaxonomy(
            level=Level.CONVERSATION,
            feature=FeatureTag(primary="support.chat"),
            source=SourceInfo(type=DatasetSource.HANDCRAFTED),
            ground_truth=GroundTruthSpec(methods=[GroundTruthMethod.RUBRIC]),
            dataset_type=DatasetType.CAPABILITY,
        ),
        expected=Expected(),
    )


def _history_echo(req: AdapterRequest) -> AdapterResponse:
    """Reply with the count of messages it was handed — proves history grows."""
    n = len(req.input.get("messages", []))
    return AdapterResponse(content=f"seen:{n}", tokens_input=n, tokens_output=1)


def _executor(fn: Any) -> MultiTurnExecutor:
    agent = _agent()
    inner = Executor(
        adapter=EmbeddedAdapter(fn, agent=agent),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    return MultiTurnExecutor(inner)


@pytest.mark.asyncio
async def test_one_trace_per_turn_sharing_thread() -> None:
    case = _conversation_case(
        [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "scripted"},
            {"role": "user", "content": "u2"},
            {"role": "user", "content": "u3"},
        ]
    )
    run = await _executor(_history_echo).run_case(case)
    # 3 user turns -> 3 traces.
    assert len(run.repetitions) == 3
    thread_ids = {r.trace.run.thread_id for r in run.repetitions}
    assert len(thread_ids) == 1 and None not in thread_ids
    positions = [r.trace.run.thread_position for r in run.repetitions]
    assert positions == [0, 1, 2]


@pytest.mark.asyncio
async def test_history_accumulates_across_turns() -> None:
    case = _conversation_case(
        [
            {"role": "user", "content": "u1"},
            {"role": "user", "content": "u2"},
        ]
    )
    run = await _executor(_history_echo).run_case(case)
    # Turn 0 sees [u1] -> "seen:1". Turn 1 sees [u1, assistant(seen:1), u2] -> "seen:3".
    assert run.repetitions[0].response is not None
    assert run.repetitions[0].response.content == "seen:1"
    assert run.repetitions[1].response is not None
    assert run.repetitions[1].response.content == "seen:3"


@pytest.mark.asyncio
async def test_adapter_error_stops_thread() -> None:
    calls = {"n": 0}

    def _fail_on_second(req: AdapterRequest) -> AdapterResponse:
        calls["n"] += 1
        if calls["n"] == 2:
            raise AdapterError("turn 2 blew up")
        return AdapterResponse(content="ok", tokens_input=1, tokens_output=1)

    case = _conversation_case(
        [
            {"role": "user", "content": "u1"},
            {"role": "user", "content": "u2"},
            {"role": "user", "content": "u3"},
        ]
    )
    run = await _executor(_fail_on_second).run_case(case)
    # Stops after the failing turn: only 2 traces (turn 0 ok, turn 1 errored).
    assert len(run.repetitions) == 2
    assert run.repetitions[0].error is None
    assert run.repetitions[1].error is not None
    # Turn 3 never ran.
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_multiple_repetitions_are_distinct_threads() -> None:
    case = _conversation_case([{"role": "user", "content": "u1"}])
    run = await _executor(_history_echo).run_case(case, repetitions=2)
    assert len(run.repetitions) == 2
    thread_ids = {r.trace.run.thread_id for r in run.repetitions}
    assert len(thread_ids) == 2


@pytest.mark.asyncio
async def test_collapse_turns_into_per_thread_funnel() -> None:
    from selfevals.graders.base import GradeLabel, GradeResult
    from selfevals.optimization.loop import _collapse_conversation_turns

    case = _conversation_case(
        [
            {"role": "user", "content": "u1"},
            {"role": "user", "content": "u2"},
        ]
    )
    run = await _executor(_history_echo).run_case(case)
    # One grade per turn: turn 0 fails, turn 1 passes (final turn authoritative).
    grades_per_turn = [
        [GradeResult(grader="g", label=GradeLabel.FAIL, reason="t0", score=0.0)],
        [GradeResult(grader="g", label=GradeLabel.PASS, reason="t1", score=1.0)],
    ]
    collapsed_run, collapsed_grades = _collapse_conversation_turns(run, grades_per_turn)
    # Two turns of one thread collapse to a single repetition.
    assert len(collapsed_run.repetitions) == 1
    assert len(collapsed_grades) == 1
    grade = collapsed_grades[0][0]
    # Final turn authoritative.
    assert grade.label == GradeLabel.PASS
    assert grade.breakdown is not None
    assert grade.breakdown.key == "conversation"
    # One advisory (weight=0) child per turn, in order.
    child_keys = [c.key for c in grade.breakdown.children]
    assert child_keys == ["turn_0", "turn_1"]
    assert all(c.weight == 0.0 for c in grade.breakdown.children)
    assert grade.breakdown.children[0].label == GradeLabel.FAIL
    assert grade.breakdown.children[1].label == GradeLabel.PASS


@pytest.mark.asyncio
async def test_rejects_non_conversation_case() -> None:
    case = EvalCase(
        id=EvalCase.make_id(),
        workspace_id=WS,
        name="opaque",
        task_type="smoke",
        input={"prompt": "no messages key"},
        taxonomy=CaseTaxonomy(
            level=Level.FINAL_RESPONSE,
            feature=FeatureTag(primary="support.chat"),
            source=SourceInfo(type=DatasetSource.HANDCRAFTED),
            ground_truth=GroundTruthSpec(methods=[GroundTruthMethod.RUBRIC]),
            dataset_type=DatasetType.CAPABILITY,
        ),
        expected=Expected(),
    )
    with pytest.raises(ValueError, match="conversation"):
        await _executor(_history_echo).run_case(case)
