from __future__ import annotations

from typing import Any

import pytest

from selfevals.runner.adapters import (
    AdapterRequest,
    AdapterResponse,
    AdapterToolUse,
    EmbeddedAdapter,
)
from selfevals.runner.executor import Executor
from selfevals.runner.sandbox import SandboxPolicy
from selfevals.schemas.enums import (
    AgentType,
    DatasetSource,
    DatasetType,
    GroundTruthMethod,
    Level,
    SandboxMode,
    StopReason,
    ToolCallStatus,
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
from selfevals.schemas.trace import LLMCallSpan, ToolCallSpan

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _case(**overrides: Any) -> EvalCase:
    base: dict[str, Any] = {
        "id": EvalCase.make_id(),
        "workspace_id": WS,
        "name": "echo",
        "task_type": "smoke",
        "input": {"messages": [{"role": "user", "content": "hi"}]},
        "taxonomy": CaseTaxonomy(
            level=Level.FINAL_RESPONSE,
            feature=FeatureTag(primary="commerce.product_resolution"),
            source=SourceInfo(type=DatasetSource.HANDCRAFTED),
            ground_truth=GroundTruthSpec(methods=[GroundTruthMethod.EXACT_MATCH]),
            dataset_type=DatasetType.CAPABILITY,
        ),
        "expected": Expected(must_include=["pong"]),
    }
    base.update(overrides)
    return EvalCase(**base)


def _agent() -> Agent:
    return Agent(
        id=Agent.make_id(),
        workspace_id=WS,
        agent_type=AgentType.SYSTEM_PROMPT,
        model=ModelRef(provider="anthropic", name="claude-sonnet-4-6"),
        system_prompt_pointer="oss://prompts/echo",
    )


def _ping(req: AdapterRequest) -> AdapterResponse:
    return AdapterResponse(
        content="pong",
        tokens_input=4,
        tokens_output=2,
        stop_reason="end_turn",
    )


def _tool_user(req: AdapterRequest) -> AdapterResponse:
    return AdapterResponse(
        content="ok",
        tokens_input=5,
        tokens_output=3,
        stop_reason="tool_use",
        tool_uses=[AdapterToolUse(tool="search", tool_use_id="toolu_01")],
    )


def _boom(req: AdapterRequest) -> AdapterResponse:
    raise RuntimeError("kaboom")


@pytest.mark.asyncio
async def test_executor_runs_one_repetition() -> None:
    agent = _agent()
    adapter = EmbeddedAdapter(_ping, agent=agent)
    executor = Executor(
        adapter=adapter,
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    run = await executor.run_case(_case())
    assert len(run.repetitions) == 1
    rep = run.repetitions[0]
    assert rep.error is None
    assert rep.response is not None
    assert rep.response.content == "pong"
    assert rep.trace.metrics.llm_call_count == 1
    # Ensure the agent turn and llm span are present.
    kinds = {type(s) for s in rep.trace.spans}
    assert LLMCallSpan in kinds


@pytest.mark.asyncio
async def test_executor_records_tool_calls_with_use_id_linkage() -> None:
    agent = _agent()
    executor = Executor(
        adapter=EmbeddedAdapter(_tool_user, agent=agent),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    run = await executor.run_case(_case())
    rep = run.repetitions[0]
    tool_spans = [s for s in rep.trace.spans if isinstance(s, ToolCallSpan)]
    assert len(tool_spans) == 1
    assert tool_spans[0].tool_use_id == "toolu_01"
    assert tool_spans[0].status == ToolCallStatus.OK
    assert tool_spans[0].sandboxed is True  # mock mode → always sandboxed


@pytest.mark.asyncio
async def test_executor_records_stop_reason() -> None:
    executor = Executor(
        adapter=EmbeddedAdapter(_ping, agent=_agent()),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    rep = (await executor.run_case(_case())).repetitions[0]
    llm_span = next(s for s in rep.trace.spans if isinstance(s, LLMCallSpan))
    assert llm_span.output.stop_reason == StopReason.END_TURN


@pytest.mark.asyncio
async def test_executor_marks_failed_repetition() -> None:
    executor = Executor(
        adapter=EmbeddedAdapter(_boom, agent=_agent()),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    run = await executor.run_case(_case())
    rep = run.repetitions[0]
    assert rep.error is not None
    assert "kaboom" in rep.error
    assert rep.response is None
    # Trace should reflect ERRORED final state.
    assert rep.trace.final_state.status.value == "errored"
    assert run.failed_count == 1
    assert run.successful_count == 0


@pytest.mark.asyncio
async def test_executor_runs_multiple_repetitions() -> None:
    executor = Executor(
        adapter=EmbeddedAdapter(_ping, agent=_agent()),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    run = await executor.run_case(_case(), repetitions=3)
    assert len(run.repetitions) == 3
    assert {r.repetition for r in run.repetitions} == {0, 1, 2}


@pytest.mark.asyncio
async def test_executor_rejects_invalid_repetitions() -> None:
    executor = Executor(
        adapter=EmbeddedAdapter(_ping, agent=_agent()),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    with pytest.raises(ValueError):
        await executor.run_case(_case(), repetitions=0)


def test_executor_rejects_invalid_concurrency() -> None:
    with pytest.raises(ValueError):
        Executor(
            adapter=EmbeddedAdapter(_ping, agent=_agent()),
            sandbox=SandboxPolicy(SandboxMode.MOCK),
            workspace_id=WS,
            concurrency=0,
        )


def test_executor_requires_workspace() -> None:
    with pytest.raises(ValueError):
        Executor(
            adapter=EmbeddedAdapter(_ping, agent=_agent()),
            sandbox=SandboxPolicy(SandboxMode.MOCK),
            workspace_id="",
        )


def test_executor_blocks_live_sandbox_at_construction() -> None:
    from selfevals.runner.sandbox import SandboxViolationError

    with pytest.raises(SandboxViolationError):
        Executor(
            adapter=EmbeddedAdapter(_ping, agent=_agent()),
            sandbox=SandboxPolicy(SandboxMode.LIVE_CANARY),
            workspace_id=WS,
        )


@pytest.mark.asyncio
async def test_executor_no_agent_still_produces_trace() -> None:
    # AgentSnapshotRef falls back to 'unknown' so adapters without an
    # attached Agent still produce a valid Trace.
    executor = Executor(
        adapter=EmbeddedAdapter(_ping),  # no agent attached
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    rep = (await executor.run_case(_case())).repetitions[0]
    assert rep.trace.agent.agent_id == "unknown"
    assert rep.error is None


@pytest.mark.asyncio
async def test_executor_repetitions_run_concurrently() -> None:
    import asyncio
    import time

    delay = 0.2

    async def slow(req: AdapterRequest) -> AdapterResponse:
        await asyncio.sleep(delay)
        return AdapterResponse(content="pong", stop_reason="end_turn")

    executor = Executor(
        adapter=EmbeddedAdapter(slow, agent=_agent()),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
        concurrency=8,
    )
    start = time.perf_counter()
    run = await executor.run_case(_case(), repetitions=5)
    elapsed = time.perf_counter() - start
    assert len(run.repetitions) == 5
    # 5 reps each sleeping `delay`; if sequential, elapsed >= 5*delay. Concurrent
    # execution should finish in well under the sequential sum.
    assert elapsed < delay * 5 * 0.6


@pytest.mark.asyncio
async def test_executor_preserves_repetition_order_under_concurrency() -> None:
    import asyncio

    # Reverse the natural completion order: rep 0 sleeps longest so, if order
    # were determined by completion, it would land last. gather must restore it.
    counter = {"n": 0}

    async def staggered(req: AdapterRequest) -> AdapterResponse:
        idx = counter["n"]
        counter["n"] += 1
        await asyncio.sleep(0.05 * (5 - idx))
        return AdapterResponse(content=f"rep-{idx}", stop_reason="end_turn")

    executor = Executor(
        adapter=EmbeddedAdapter(staggered, agent=_agent()),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
        concurrency=8,
    )
    run = await executor.run_case(_case(), repetitions=5)
    assert [r.repetition for r in run.repetitions] == [0, 1, 2, 3, 4]


@pytest.mark.asyncio
async def test_executor_one_rep_errors_others_succeed() -> None:
    import asyncio

    from selfevals.runner.adapters import AdapterError

    counter = {"n": 0}

    async def flaky(req: AdapterRequest) -> AdapterResponse:
        idx = counter["n"]
        counter["n"] += 1
        await asyncio.sleep(0.01)
        if idx == 1:
            raise AdapterError("rep 1 boom")
        return AdapterResponse(content="pong", stop_reason="end_turn")

    executor = Executor(
        adapter=EmbeddedAdapter(flaky, agent=_agent()),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    run = await executor.run_case(_case(), repetitions=4)
    assert run.successful_count == 3
    assert run.failed_count == 1
    errored = [r for r in run.repetitions if r.error is not None]
    assert len(errored) == 1
    assert "boom" in errored[0].error  # type: ignore[operator]
