from __future__ import annotations

from typing import Any

import pytest

from selfevals.runner.adapters import (
    AdapterError,
    AdapterRequest,
    AdapterResponse,
    EmbeddedAdapter,
)
from selfevals.runner.executor import Executor
from selfevals.runner.multiturn import MultiTurnExecutor
from selfevals.runner.sandbox import SandboxPolicy
from selfevals.runner.simulator import (
    ROLE_TAG_USER_SIMULATOR,
    SimulatorSpec,
    UserSimulator,
)
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


def _conversation_case(
    messages: list[dict[str, Any]],
    *,
    simulator_block: dict[str, Any] | None = None,
) -> EvalCase:
    input_payload: dict[str, Any] = {"messages": messages}
    if simulator_block is not None:
        input_payload["simulator"] = simulator_block
    return EvalCase(
        id=EvalCase.make_id(),
        workspace_id=WS,
        name="chat",
        task_type="smoke",
        input=input_payload,
        taxonomy=CaseTaxonomy(
            level=Level.CONVERSATION,
            feature=FeatureTag(primary="support.chat"),
            source=SourceInfo(type=DatasetSource.SIMULATION),
            ground_truth=GroundTruthSpec(methods=[GroundTruthMethod.RUBRIC]),
            dataset_type=DatasetType.CAPABILITY,
        ),
        expected=Expected(),
    )


def _executor(fn: Any) -> MultiTurnExecutor:
    agent = _agent()
    inner = Executor(
        adapter=EmbeddedAdapter(fn, agent=agent),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    return MultiTurnExecutor(inner)


def _spec(**overrides: Any) -> SimulatorSpec:
    base: dict[str, Any] = {
        "persona": "an impatient customer",
        "goal": "get a refund",
        "success_criteria": [],
        "stop_condition": None,
        "max_turns": 4,
    }
    base.update(overrides)
    return SimulatorSpec(**base)


# ---------- SimulatorSpec contract ----------


def test_spec_rejects_empty_persona_goal() -> None:
    with pytest.raises(ValueError, match="persona"):
        SimulatorSpec(persona="", goal="x")
    with pytest.raises(ValueError, match="goal"):
        SimulatorSpec(persona="x", goal="")
    with pytest.raises(ValueError, match="max_turns"):
        SimulatorSpec(persona="x", goal="y", max_turns=0)


def test_spec_from_dict_accepts_documented_fields() -> None:
    spec = SimulatorSpec.from_dict(
        {
            "persona": "p",
            "goal": "g",
            "success_criteria": ["refund issued"],
            "stop_condition": "I give up",
            "max_turns": 6,
            "unknown_field": "ignored",
        }
    )
    assert spec.persona == "p"
    assert spec.success_criteria == ["refund issued"]
    assert spec.stop_condition == "I give up"
    assert spec.max_turns == 6


def test_spec_success_and_stop_substring_match_case_insensitive() -> None:
    spec = _spec(success_criteria=["Refund Issued"], stop_condition="GIVE UP")
    assert spec.is_success("Great news, your refund issued today.")
    assert not spec.is_success("we will look into it")
    assert not spec.is_success(None)
    assert spec.hit_stop_condition("I have to give up here")
    assert not spec.hit_stop_condition(None)


# ---------- UserSimulator core ----------


@pytest.mark.asyncio
async def test_simulator_requires_replies_or_judge() -> None:
    with pytest.raises(ValueError):
        UserSimulator(_spec())


@pytest.mark.asyncio
async def test_scripted_simulator_returns_replies_in_order_with_tag() -> None:
    sim = UserSimulator(_spec(max_turns=5), scripted_replies=["a", "b", "c"])
    req = AdapterRequest(workspace_id=WS, case_id="ec_x", input={"messages": []})
    r1 = await sim.invoke(req)
    r2 = await sim.invoke(req)
    r3 = await sim.invoke(req)
    assert [r1.content, r2.content, r3.content] == ["a", "b", "c"]
    for r in (r1, r2, r3):
        assert r.provider_metadata["role_tag"] == ROLE_TAG_USER_SIMULATOR
        assert r.provider_metadata["source"] == "scripted"
    # Past the end: empty content, end_turn.
    r4 = await sim.invoke(req)
    assert r4.content == ""
    assert r4.stop_reason == "end_turn"
    assert r4.provider_metadata["termination"] == "scripted_exhausted"


@pytest.mark.asyncio
async def test_scripted_simulator_caps_at_max_turns() -> None:
    sim = UserSimulator(_spec(max_turns=2), scripted_replies=["a", "b", "c"])
    req = AdapterRequest(workspace_id=WS, case_id="ec_x", input={"messages": []})
    await sim.invoke(req)
    await sim.invoke(req)
    r3 = await sim.invoke(req)
    assert r3.content == ""
    assert r3.provider_metadata["termination"] == "max_turns"


@pytest.mark.asyncio
async def test_judge_mode_uses_wrapped_adapter() -> None:
    seen: list[dict[str, Any]] = []

    def _judge(req: AdapterRequest) -> AdapterResponse:
        seen.append(req.input)
        return AdapterResponse(content="please refund", cost_usd=0.0123)

    sim = UserSimulator(
        _spec(),
        judge_adapter=EmbeddedAdapter(_judge),
    )
    req = AdapterRequest(
        workspace_id=WS,
        case_id="ec_x",
        input={"messages": [{"role": "assistant", "content": "hello"}]},
    )
    resp = await sim.invoke(req)
    assert resp.content == "please refund"
    assert resp.cost_usd == 0.0123
    assert resp.provider_metadata["role_tag"] == ROLE_TAG_USER_SIMULATOR
    assert resp.provider_metadata["source"] == "judge"
    assert seen and seen[0]["messages"][0]["role"] == "assistant"
    assert "Persona:" in seen[0]["system_prompt"]


@pytest.mark.asyncio
async def test_judge_mode_falls_through_after_scripted() -> None:
    judge_calls = {"n": 0}

    def _judge(_req: AdapterRequest) -> AdapterResponse:
        judge_calls["n"] += 1
        return AdapterResponse(content="generated")

    sim = UserSimulator(
        _spec(max_turns=4),
        scripted_replies=["scripted-1"],
        judge_adapter=EmbeddedAdapter(_judge),
    )
    req = AdapterRequest(workspace_id=WS, case_id="ec_x", input={"messages": []})
    r1 = await sim.invoke(req)
    r2 = await sim.invoke(req)
    assert r1.content == "scripted-1"
    assert r1.provider_metadata["source"] == "scripted"
    assert r2.content == "generated"
    assert r2.provider_metadata["source"] == "judge"
    assert judge_calls["n"] == 1


@pytest.mark.asyncio
async def test_simulator_reset_restarts_scripted_pointer() -> None:
    sim = UserSimulator(_spec(), scripted_replies=["a", "b"])
    req = AdapterRequest(workspace_id=WS, case_id="ec_x", input={"messages": []})
    await sim.invoke(req)
    assert sim.turns_emitted == 1
    sim.reset()
    assert sim.turns_emitted == 0
    r1 = await sim.invoke(req)
    assert r1.content == "a"


# ---------- MultiTurnExecutor interleave ----------


def _history_echo(req: AdapterRequest) -> AdapterResponse:
    """Reply with the count of messages it was handed."""
    n = len(req.input.get("messages", []))
    return AdapterResponse(content=f"seen:{n}", tokens_input=n, tokens_output=1)


@pytest.mark.asyncio
async def test_executor_interleaves_scripted_then_simulator() -> None:
    case = _conversation_case([{"role": "user", "content": "u1"}])
    sim = UserSimulator(
        _spec(max_turns=3),
        scripted_replies=["u2", "u3"],
    )
    run = await _executor(_history_echo).run_case(case, simulator=sim)
    # 1 scripted user turn + 2 simulator turns = 3 SUT invocations.
    assert len(run.repetitions) == 3
    positions = [r.trace.run.thread_position for r in run.repetitions]
    assert positions == [0, 1, 2]
    thread_ids = {r.trace.run.thread_id for r in run.repetitions}
    assert len(thread_ids) == 1
    # History growth: turn0 sees [u1] -> seen:1. Turn1 sees [u1, assistant,
    # simulator user] -> seen:3. Turn2 sees prev+assistant+sim -> seen:5.
    assert run.repetitions[0].response is not None
    assert run.repetitions[0].response.content == "seen:1"
    assert run.repetitions[1].response is not None
    assert run.repetitions[1].response.content == "seen:3"
    assert run.repetitions[2].response is not None
    assert run.repetitions[2].response.content == "seen:5"
    # The simulator emitted both turns.
    assert run.simulator_turns == 2


@pytest.mark.asyncio
async def test_executor_stops_at_max_turns() -> None:
    case = _conversation_case([{"role": "user", "content": "u1"}])
    sim = UserSimulator(
        _spec(max_turns=1),
        scripted_replies=["sim1", "sim2", "sim3"],
    )
    run = await _executor(_history_echo).run_case(case, simulator=sim)
    # 1 scripted + 1 simulator (capped by max_turns=1) = 2 SUT calls.
    assert len(run.repetitions) == 2
    assert run.simulator_turns == 1


@pytest.mark.asyncio
async def test_executor_stops_at_success_criteria_match() -> None:
    """When the SUT reply satisfies success_criteria, the simulator yields."""
    case = _conversation_case([{"role": "user", "content": "u1"}])
    sim = UserSimulator(
        _spec(max_turns=10, success_criteria=["seen:3"]),
        scripted_replies=["u2", "u3", "u4"],
    )
    run = await _executor(_history_echo).run_case(case, simulator=sim)
    # Turn 0 -> seen:1. Sim emits u2 -> turn 1 sees [u1, asst:seen:1, u2] = seen:3.
    # After turn 1 the SUT reply contains "seen:3" which is the success
    # criterion: the simulator stops *before* producing a 3rd user turn.
    assert len(run.repetitions) == 2
    assert run.simulator_turns == 1


@pytest.mark.asyncio
async def test_executor_stops_at_stop_condition() -> None:
    case = _conversation_case([{"role": "user", "content": "u1"}])
    sim = UserSimulator(
        _spec(max_turns=10, stop_condition="seen:3"),
        scripted_replies=["u2", "u3", "u4"],
    )
    run = await _executor(_history_echo).run_case(case, simulator=sim)
    assert len(run.repetitions) == 2
    assert run.simulator_turns == 1


@pytest.mark.asyncio
async def test_simulator_cost_excluded_from_sut_trace() -> None:
    """The simulator's LLM cost lives on CaseRun, not on the SUT trace.metrics."""

    def _judge(_req: AdapterRequest) -> AdapterResponse:
        # Simulator's wrapped adapter reports a non-trivial cost; this must
        # NOT leak into any of the SUT traces.
        return AdapterResponse(content="next user turn", cost_usd=0.0500)

    sim = UserSimulator(
        _spec(max_turns=2),
        judge_adapter=EmbeddedAdapter(_judge),
    )
    case = _conversation_case([{"role": "user", "content": "u1"}])
    run = await _executor(_history_echo).run_case(case, simulator=sim)

    # The SUT trace cost may be non-zero (pricing tables convert SUT tokens
    # into a per-call USD), but it MUST NOT include the simulator's
    # $0.05/turn — we assert the simulator cost lives on the CaseRun
    # aggregate and is strictly larger than anything attributable to the
    # SUT side (which only emits 1 output token per call).
    sut_total_cost = sum(r.trace.metrics.total_cost_usd for r in run.repetitions)
    assert run.simulator_turns >= 1
    assert run.simulator_cost_usd == pytest.approx(0.0500 * run.simulator_turns)
    # SUT cost is a tiny pricing-table figure (cents-per-million tokens at
    # 1 token/turn). Simulator cost is two orders of magnitude larger and
    # is reported separately — they never overlap.
    assert sut_total_cost < run.simulator_cost_usd / 100


@pytest.mark.asyncio
async def test_executor_ignores_input_simulator_block_when_no_runtime_wired() -> None:
    """A case carrying a `simulator:` block but no UserSimulator passed in
    completes its scripted turns and stops (no error, no extra turns)."""
    case = _conversation_case(
        [{"role": "user", "content": "u1"}],
        simulator_block={
            "persona": "p",
            "goal": "g",
            "max_turns": 4,
        },
    )
    run = await _executor(_history_echo).run_case(case)
    assert len(run.repetitions) == 1
    assert run.simulator_turns == 0


@pytest.mark.asyncio
async def test_simulator_block_does_not_leak_into_sut_input() -> None:
    """The runner-internal `simulator:` key must be stripped from the per-turn
    input handed to the SUT adapter."""
    captured: list[dict[str, Any]] = []

    def _capture(req: AdapterRequest) -> AdapterResponse:
        captured.append(dict(req.input))
        return AdapterResponse(content="ok", tokens_output=1)

    case = _conversation_case(
        [{"role": "user", "content": "u1"}],
        simulator_block={"persona": "p", "goal": "g"},
    )
    await _executor(_capture).run_case(case)
    assert captured
    assert "simulator" not in captured[0]
    assert "messages" in captured[0]


@pytest.mark.asyncio
async def test_simulator_failure_stops_thread_before_sut_call() -> None:
    """If the simulator raises, we do not invoke the SUT for that turn."""
    sut_calls = {"n": 0}

    def _sut(_req: AdapterRequest) -> AdapterResponse:
        sut_calls["n"] += 1
        return AdapterResponse(content="ok", tokens_output=1)

    def _broken_judge(_req: AdapterRequest) -> AdapterResponse:
        raise AdapterError("simulator imploded")

    sim = UserSimulator(_spec(max_turns=2), judge_adapter=EmbeddedAdapter(_broken_judge))
    case = _conversation_case([{"role": "user", "content": "u1"}])
    run = await _executor(_sut).run_case(case, simulator=sim)
    # Scripted turn ran (1 SUT call), simulator failed before turn 2 -> only 1.
    assert sut_calls["n"] == 1
    assert len(run.repetitions) == 1
    assert run.simulator_turns == 0
