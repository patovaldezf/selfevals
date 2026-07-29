"""End-to-end proof that `search_space.agents` swaps the running agent.

The proposer tests assert the right dict is produced. These assert the dict
actually reaches a different adapter: two embedded agents that return different
answers, and a grid that must run each one. If the binding axis were still
ignored, both iterations would return the first agent's answer and the second
assertion would fail.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

import pytest

from selfevals.repo.loader import build_spec_from_mapping
from selfevals.runner.adapters import AgentAdapter
from selfevals.runner.launch import build_loop

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def agent_alpha(request: dict[str, object]) -> str:
    """Embedded agent A — always answers 'alpha'."""
    return "alpha"


def agent_beta(request: dict[str, object]) -> str:
    """Embedded agent B — always answers 'beta'."""
    return "beta"


def _spec_mapping(*, agents: list[dict[str, object]]) -> dict[str, object]:
    """Minimal two-iteration spec whose grader accepts either answer.

    The grader must pass for both agents: this test is about *which agent ran*,
    not about scoring, and a failing grade would mask the signal.
    """
    return {
        "workspace": WS,
        "experiment": {
            "name": "agent axis",
            "goal": "prove the binding axis runs different agents",
            "mode": "handoff",
            "taxonomy": {
                "target_features": ["commerce.product_resolution"],
                "dataset_types": ["capability"],
            },
            "target": {"primary": {"name": "pass@1", "operator": ">=", "value": 0.0}},
            "datasets": {"optimization": {"id": "ds_x", "version": 1}},
            "frozen": {
                "fleet": {"id": "flt_x"},
                "agents": [{"id": "ag_x"}],
                "datasets": [{"id": "ds_x"}],
            },
            "editable": {"model_choice": True},
            "proposer": {"strategy": "grid"},
            "run": {"sandbox": "mock", "max_iterations": 2, "persist_traces": "all"},
            "search_space": {"agents": agents},
        },
        # The declared agent is alpha; the search space must override it.
        "agent": {"type": "embedded", "entrypoint": f"{__name__}:agent_alpha"},
        "graders": [{"type": "deterministic", "name": "any"}],
        "dataset": {
            "cases_inline": [
                {
                    "name": "single",
                    "task_type": "echo",
                    "input": {"messages": [{"role": "user", "content": "hi"}]},
                    "expected": {},
                    "taxonomy": {
                        "level": "final_response",
                        "feature": {"primary": "commerce.product_resolution"},
                        "source": {"type": "handcrafted"},
                        "ground_truth": {"methods": ["exact_match"]},
                        "dataset_type": "capability",
                    },
                }
            ]
        },
    }


@pytest.mark.asyncio
async def test_grid_over_agents_runs_each_declared_agent() -> None:
    """Iteration 0 runs alpha, iteration 1 runs beta — verified by their output."""
    spec = build_spec_from_mapping(
        _spec_mapping(
            agents=[
                {"type": "embedded", "entrypoint": f"{__name__}:agent_alpha"},
                {"type": "embedded", "entrypoint": f"{__name__}:agent_beta"},
            ]
        ),
        workspace_id=WS,
    )
    loop = build_loop(spec, scope=None, repetitions_per_case=1)
    try:
        result = await loop.run()
    finally:
        await loop._executor.aclose()

    answers = [
        rep.response.content
        for outcome in result.iterations
        for case_run in outcome.case_runs
        for rep in case_run.repetitions
        if rep.response is not None
    ]
    assert answers == ["alpha", "beta"], (
        "each iteration must invoke the agent its proposal bound, not the "
        f"declared default; got {answers}"
    )


@pytest.mark.asyncio
async def test_adapter_is_built_once_per_distinct_agent() -> None:
    """N repetitions over one agent build one adapter, not N.

    Building an adapter validates a cwd or opens an HTTP client, so rebuilding
    it per case x rep would be wasteful and, for stateful adapters, wrong.
    Counting factory calls (rather than inspecting the cache afterwards) is the
    durable assertion: `loop.run()` closes the executor on exit, which releases
    the cache by design.
    """
    spec = build_spec_from_mapping(
        _spec_mapping(agents=[{"type": "embedded", "entrypoint": f"{__name__}:agent_beta"}]),
        workspace_id=WS,
    )
    spec.experiment.run.max_iterations = 1
    loop = build_loop(spec, scope=None, repetitions_per_case=3)

    executor = loop._executor
    real_factory = executor._adapter_factory
    assert real_factory is not None, "build_loop must install an adapter factory"
    calls: list[Mapping[str, object]] = []

    def _counting(block: Mapping[str, object]) -> AgentAdapter:
        calls.append(block)
        return real_factory(block)

    executor.set_adapter_factory(_counting)
    await loop.run()

    assert len(calls) == 1, f"adapter rebuilt {len(calls)}x for one agent across 3 reps"


@pytest.mark.asyncio
async def test_aclose_releases_cached_adapters() -> None:
    """Teardown must drop every adapter the sweep built, not just the default."""
    spec = build_spec_from_mapping(
        _spec_mapping(agents=[{"type": "embedded", "entrypoint": f"{__name__}:agent_beta"}]),
        workspace_id=WS,
    )
    spec.experiment.run.max_iterations = 1
    loop = build_loop(spec, scope=None, repetitions_per_case=1)
    await loop.run()
    assert loop._executor._adapter_cache == {}


def test_agent_override_without_factory_is_a_clear_error() -> None:
    """A bare Executor must refuse an agent override rather than run the wrong one.

    Library callers that construct an `Executor` directly never installed a
    factory; silently falling back to the declared adapter would report results
    for an agent that never ran.
    """
    from selfevals.runner.adapters import AdapterRequest, AdapterResponse
    from selfevals.runner.executor import Executor
    from selfevals.runner.sandbox import SandboxPolicy
    from selfevals.schemas.enums import SandboxMode

    class _Stub:
        agent = None

        async def invoke(self, request: AdapterRequest) -> AdapterResponse:
            raise AssertionError("must not be reached")

    executor = Executor(
        adapter=_Stub(),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    with pytest.raises(ValueError, match="no adapter factory"):
        executor.agent_ref({"agent": {"type": "embedded", "entrypoint": "x:y"}})

    asyncio.run(executor.aclose())
