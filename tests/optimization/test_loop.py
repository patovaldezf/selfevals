from __future__ import annotations

import logging
from typing import Any

import pytest

from selfevals.graders.deterministic import DeterministicGrader
from selfevals.optimization.loop import OptimizationLoop
from selfevals.optimization.proposers import GridProposer, ManualProposer
from selfevals.runner.adapters import AdapterRequest, AdapterResponse, EmbeddedAdapter
from selfevals.runner.executor import Executor
from selfevals.runner.sandbox import SandboxPolicy
from selfevals.schemas._base import EntityRef
from selfevals.schemas.enums import (
    AgentType,
    DatasetSource,
    DatasetType,
    DecisionOutcome,
    ExperimentState,
    GroundTruthMethod,
    Level,
    Mode,
    ProposerStrategy,
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
from selfevals.schemas.experiment import (
    ConvergenceSpec,
    DatasetUsage,
    EditableContract,
    Experiment,
    ExperimentTaxonomy,
    FrozenSnapshot,
    MetricTarget,
    ProposerSpec,
    ReliabilitySpec,
    RunSpec,
    SearchSpace,
    TargetSpec,
)
from selfevals.schemas.fleet import Agent, ModelRef
from selfevals.schemas.iteration import DecisionRecord, IterationRecord
from selfevals.schemas.workspace import Workspace
from selfevals.storage.factory import open_storage

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _case(content_target: str = "pong", *, holdout: bool = False) -> EvalCase:
    return EvalCase(
        id=EvalCase.make_id(),
        workspace_id=WS,
        name="t",
        task_type="x",
        input={"messages": [{"role": "user", "content": "hi"}]},
        taxonomy=CaseTaxonomy(
            level=Level.FINAL_RESPONSE,
            feature=FeatureTag(primary="commerce.product_resolution"),
            source=SourceInfo(type=DatasetSource.HANDCRAFTED),
            ground_truth=GroundTruthSpec(methods=[GroundTruthMethod.EXACT_MATCH]),
            dataset_type=DatasetType.CAPABILITY,
        ),
        expected=Expected(must_include=[content_target]),
        holdout=holdout,
    )


def _agent() -> Agent:
    return Agent(
        id=Agent.make_id(),
        workspace_id=WS,
        agent_type=AgentType.SYSTEM_PROMPT,
        model=ModelRef(provider="anthropic", name="claude-sonnet-4-6"),
        system_prompt_pointer="oss://prompts/x",
    )


def _experiment(
    *,
    max_iterations: int = 4,
    search_space: dict[str, Any] | None = None,
    convergence: ConvergenceSpec | None = None,
    proposer_strategy: ProposerStrategy = ProposerStrategy.GRID,
    persist_traces: str = "failed",
) -> Experiment:
    return Experiment(
        id=Experiment.make_id(),
        workspace_id=WS,
        name="exp",
        goal="exp",
        mode=Mode.HANDOFF,
        taxonomy=ExperimentTaxonomy(
            target_features=["commerce.product_resolution"],
            dataset_types=[DatasetType.CAPABILITY],
        ),
        datasets=DatasetUsage(optimization=EntityRef(id="ds_x", version=1)),
        target=TargetSpec(primary=MetricTarget(name="pass@1", operator=">=", value=0.9)),
        editable=EditableContract(prompt=True, model_params=True),
        frozen=FrozenSnapshot(
            fleet=EntityRef(id="flt_x"),
            agents=[EntityRef(id="ag_x")],
            datasets=[EntityRef(id="ds_y")],
        ),
        proposer=ProposerSpec(strategy=proposer_strategy),
        run=RunSpec(
            sandbox=SandboxMode.MOCK,
            max_iterations=max_iterations,
            convergence=convergence or ConvergenceSpec(min_delta=1e-6, patience=2),
            persist_traces=persist_traces,  # type: ignore[arg-type]
        ),
        search_space=SearchSpace(model_params=search_space or {}),
        reliability=ReliabilitySpec(metrics=["pass@1"]),
    )


def _adapter_for(target: str, *, level: float) -> EmbeddedAdapter:
    """An adapter whose content depends on a model_params.level override."""

    def fn(req: AdapterRequest) -> AdapterResponse:
        # Higher level → more likely to emit `target`.
        actual_level = req.parameters.get("model_params", {}).get("level", level)
        content = target if actual_level >= 0.5 else "miss"
        return AdapterResponse(content=content, tokens_input=4, tokens_output=2)

    return EmbeddedAdapter(fn, agent=_agent())


@pytest.mark.asyncio
async def test_loop_runs_max_iterations_when_no_convergence(db_url: str) -> None:
    cases = [_case("pong")]
    exp = _experiment(
        max_iterations=3,
        search_space={"level": [0.0, 0.5, 1.0]},
    )
    storage = open_storage(db_url)
    ws_id = WS
    ws = Workspace(id=ws_id, workspace_id=ws_id, slug="t", name="t")
    with storage.open(ws_id) as scope:
        scope.put_entity(ws)
    scope = storage.open(ws_id)
    executor = Executor(
        adapter=_adapter_for("pong", level=1.0),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=ws_id,
    )
    loop = OptimizationLoop(
        experiment=exp,
        executor=executor,
        proposer=GridProposer(),
        graders=[DeterministicGrader()],
        cases=cases,
        scope=scope,
    )
    result = await loop.run()
    assert exp.state == ExperimentState.COMPLETED
    assert len(result.iterations) == 3
    # Storage persisted IterationRecord + DecisionRecord per iteration.
    with storage.open(ws_id) as s:
        iters = s.list_entities(IterationRecord)
        decisions = s.list_entities(DecisionRecord)
    assert len(iters) == 3
    assert len(decisions) == 3
    storage.close()


@pytest.mark.asyncio
async def test_loop_persists_experiment_terminal_state(db_url: str) -> None:
    # The experiment row in storage must reflect the run's outcome, not the
    # pre-run state. Before the fix the loop transitioned the in-memory object
    # to COMPLETED but never flushed it, so a reader (e.g. a polling HTTP
    # client) saw it stuck at DRAFT.
    cases = [_case("pong")]
    exp = _experiment(max_iterations=1, search_space={"level": [1.0]})
    assert exp.state == ExperimentState.DRAFT
    storage = open_storage(db_url)
    ws = Workspace(id=WS, workspace_id=WS, slug="t", name="t")
    with storage.open(WS) as scope:
        scope.put_entity(ws)
    scope = storage.open(WS)
    loop = OptimizationLoop(
        experiment=exp,
        executor=Executor(
            adapter=_adapter_for("pong", level=1.0),
            sandbox=SandboxPolicy(SandboxMode.MOCK),
            workspace_id=WS,
        ),
        proposer=GridProposer(),
        graders=[DeterministicGrader()],
        cases=cases,
        scope=scope,
    )
    await loop.run()
    scope.close()

    with storage.open(WS) as s:
        persisted = s.get_entity(Experiment, exp.id)
    assert isinstance(persisted, Experiment)
    assert persisted.state == ExperimentState.COMPLETED
    storage.close()


@pytest.mark.asyncio
async def test_loop_persists_and_carries_grader_reason(db_url: str) -> None:
    from selfevals.schemas.trace import Trace

    # level 0.0 → adapter emits "miss", so the must_include=["pong"] rule fails
    # and the DeterministicGrader produces a non-empty failure reason.
    cases = [_case("pong")]
    exp = _experiment(max_iterations=1, search_space={"level": [0.0]})
    storage = open_storage(db_url)
    ws = Workspace(id=WS, workspace_id=WS, slug="t", name="t")
    with storage.open(WS) as scope:
        scope.put_entity(ws)
    scope = storage.open(WS)
    executor = Executor(
        adapter=_adapter_for("pong", level=0.0),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    loop = OptimizationLoop(
        experiment=exp,
        executor=executor,
        proposer=GridProposer(),
        graders=[DeterministicGrader()],
        cases=cases,
        scope=scope,
    )
    result = await loop.run()

    # In-memory: the iteration's case-run traces carry the grader reason.
    grader_results = [
        gr
        for it in result.iterations
        for run in it.case_runs
        for rep in run.repetitions
        for gr in rep.trace.grader_results
    ]
    assert grader_results, "expected at least one stamped grader result"
    assert all(gr.reason for gr in grader_results)

    # Persisted: the failing trace was written and still carries the reason.
    with storage.open(WS) as s:
        traces = s.list_entities(Trace)
    persisted = [gr for t in traces for gr in t.grader_results]
    assert persisted, "expected the failing trace to be persisted with grader results"
    assert all(gr.reason for gr in persisted)
    storage.close()


@pytest.mark.asyncio
async def test_trace_run_ids_only_lists_persisted_traces(db_url: str) -> None:
    """`trace_run_ids` must announce only traces actually written to storage.

    With `persist_traces="failed"` (default), a passing case's trace is never
    stored — announcing its run_id made `/traces/{run_id}` 404. The record now
    lists only persisted run_ids, and every one resolves to a stored Trace.
    """
    from selfevals.api.queries import load_trace
    from selfevals.schemas.trace import Trace

    # Same adapter emits "pong" for both cases (level 1.0). One case requires
    # "pong" (passes, not persisted under "failed"); the other requires "zzz"
    # (fails, persisted).
    cases = [_case("pong"), _case("zzz")]
    exp = _experiment(max_iterations=1, search_space={"level": [1.0]})
    storage = open_storage(db_url)
    ws = Workspace(id=WS, workspace_id=WS, slug="t", name="t")
    with storage.open(WS) as scope:
        scope.put_entity(ws)
    scope = storage.open(WS)
    executor = Executor(
        adapter=_adapter_for("pong", level=1.0),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    loop = OptimizationLoop(
        experiment=exp,
        executor=executor,
        proposer=GridProposer(),
        graders=[DeterministicGrader()],
        cases=cases,
        scope=scope,
    )
    result = await loop.run()

    record = result.iterations[0].iteration_record
    announced = record.execution.trace_run_ids
    # Only the failing case's trace was persisted → exactly one announced id.
    assert len(announced) == 1
    with storage.open(WS) as s:
        stored = [t.run.run_id for t in s.list_entities(Trace)]
    assert set(announced) == set(stored)
    # Every announced run_id resolves (no 404).
    for run_id in announced:
        assert load_trace(storage, workspace_id=WS, trace_id=run_id) is not None
    storage.close()


@pytest.mark.asyncio
async def test_trace_run_ids_lists_all_when_persist_all(db_url: str) -> None:
    """With `persist_traces="all"`, every rep's trace is stored and announced."""
    from selfevals.schemas.trace import Trace

    cases = [_case("pong"), _case("zzz")]
    exp = _experiment(
        max_iterations=1, search_space={"level": [1.0]}, persist_traces="all"
    )
    storage = open_storage(db_url)
    ws = Workspace(id=WS, workspace_id=WS, slug="t", name="t")
    with storage.open(WS) as scope:
        scope.put_entity(ws)
    scope = storage.open(WS)
    executor = Executor(
        adapter=_adapter_for("pong", level=1.0),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    loop = OptimizationLoop(
        experiment=exp,
        executor=executor,
        proposer=GridProposer(),
        graders=[DeterministicGrader()],
        cases=cases,
        scope=scope,
    )
    result = await loop.run()

    announced = result.iterations[0].iteration_record.execution.trace_run_ids
    with storage.open(WS) as s:
        stored = [t.run.run_id for t in s.list_entities(Trace)]
    # Both cases (1 rep each) stored and announced.
    assert len(announced) == 2
    assert set(announced) == set(stored)
    storage.close()


@pytest.mark.asyncio
async def test_loop_terminates_on_search_space_exhausted() -> None:
    cases = [_case("pong")]
    exp = _experiment(
        max_iterations=10,
        search_space={"level": [0.0, 0.5]},  # only 2 combos
    )
    executor = Executor(
        adapter=_adapter_for("pong", level=1.0),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    loop = OptimizationLoop(
        experiment=exp,
        executor=executor,
        proposer=GridProposer(),
        graders=[DeterministicGrader()],
        cases=cases,
    )
    result = await loop.run()
    assert len(result.iterations) == 2
    assert result.terminated_reason.startswith("search_space_exhausted")


@pytest.mark.asyncio
async def test_loop_converges_when_no_improvement_for_patience() -> None:
    cases = [_case("pong")]
    # All proposals score the same (level=1.0 always → all pass).
    # Use a non-grid proposer: grid now exhausts the full search space and
    # never early-stops on convergence (see the exhaust test below), so the
    # plateau cutoff is exercised here with a manual proposer that supplies
    # more candidates than patience needs.
    exp = _experiment(
        max_iterations=10,
        proposer_strategy=ProposerStrategy.MANUAL,
        convergence=ConvergenceSpec(min_delta=0.01, patience=2),
    )
    executor = Executor(
        adapter=_adapter_for("pong", level=1.0),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    loop = OptimizationLoop(
        experiment=exp,
        executor=executor,
        proposer=ManualProposer(
            [{"model_params": {"level": 1.0}} for _ in range(5)],
        ),
        graders=[DeterministicGrader()],
        cases=cases,
    )
    result = await loop.run()
    assert result.terminated_reason == "converged"
    # patience=2 means we need >= 3 iters to detect convergence.
    assert len(result.iterations) >= 3


@pytest.mark.asyncio
async def test_grid_exhausts_full_space_despite_plateau() -> None:
    # Gap-1 fix: a plateau mid-grid must NOT cut the run short — grid's contract
    # is to enumerate the full cartesian product. With a flat-scoring space and a
    # tight convergence window that would trip a non-grid proposer, grid still
    # visits every combination and terminates on search-space exhaustion.
    cases = [_case("pong")]
    exp = _experiment(
        max_iterations=10,
        search_space={"level": [1.0, 1.0, 1.0, 1.0, 1.0]},
        convergence=ConvergenceSpec(min_delta=0.01, patience=2),
    )
    executor = Executor(
        adapter=_adapter_for("pong", level=1.0),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    loop = OptimizationLoop(
        experiment=exp,
        executor=executor,
        proposer=GridProposer(),
        graders=[DeterministicGrader()],
        cases=cases,
    )
    result = await loop.run()
    assert result.terminated_reason.startswith("search_space_exhausted")
    # All 5 grid combinations were visited — no early convergence cutoff.
    assert len(result.iterations) == 5


@pytest.mark.asyncio
async def test_grid_early_stop_override_re_enables_convergence() -> None:
    # The escape hatch: a caller who wants cheap hill-climbing over a large grid
    # can opt back into the plateau cutoff with convergence.early_stop=True.
    cases = [_case("pong")]
    exp = _experiment(
        max_iterations=10,
        search_space={"level": [1.0, 1.0, 1.0, 1.0, 1.0]},
        convergence=ConvergenceSpec(min_delta=0.01, patience=2, early_stop=True),
    )
    executor = Executor(
        adapter=_adapter_for("pong", level=1.0),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    loop = OptimizationLoop(
        experiment=exp,
        executor=executor,
        proposer=GridProposer(),
        graders=[DeterministicGrader()],
        cases=cases,
    )
    result = await loop.run()
    assert result.terminated_reason == "converged"
    # Cut short before all 5 combinations: plateau detected at patience=2.
    assert len(result.iterations) < 5


@pytest.mark.asyncio
async def test_early_stop_false_forces_non_grid_to_exhaust() -> None:
    # The other override direction: a manual proposer that would normally
    # early-stop on a plateau is forced to run its whole list.
    cases = [_case("pong")]
    exp = _experiment(
        max_iterations=10,
        proposer_strategy=ProposerStrategy.MANUAL,
        convergence=ConvergenceSpec(min_delta=0.01, patience=2, early_stop=False),
    )
    executor = Executor(
        adapter=_adapter_for("pong", level=1.0),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    loop = OptimizationLoop(
        experiment=exp,
        executor=executor,
        proposer=ManualProposer(
            [{"model_params": {"level": 1.0}} for _ in range(4)],
        ),
        graders=[DeterministicGrader()],
        cases=cases,
    )
    result = await loop.run()
    assert result.terminated_reason.startswith("search_space_exhausted")
    assert len(result.iterations) == 4


@pytest.mark.asyncio
async def test_loop_best_iteration_tracks_highest_primary() -> None:
    cases = [_case("pong")]
    # Build proposals manually so we can stage scores.
    exp = _experiment(max_iterations=3, search_space={"level": [0.0, 0.5, 1.0]})
    # Adapter consults proposal model_params.level.
    executor = Executor(
        adapter=_adapter_for("pong", level=0.0),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    loop = OptimizationLoop(
        experiment=exp,
        executor=executor,
        proposer=GridProposer(),
        graders=[DeterministicGrader()],
        cases=cases,
    )
    result = await loop.run()
    best = result.best_iteration
    assert best is not None
    assert best.aggregate.primary_value == 1.0  # the level>=0.5 iters all pass


@pytest.mark.asyncio
async def test_loop_persists_decision_record_with_outcome() -> None:
    cases = [_case("pong")]
    exp = _experiment(max_iterations=1, search_space={"level": [1.0]})
    executor = Executor(
        adapter=_adapter_for("pong", level=1.0),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    loop = OptimizationLoop(
        experiment=exp,
        executor=executor,
        proposer=GridProposer(),
        graders=[DeterministicGrader()],
        cases=cases,
    )
    result = await loop.run()
    outcome = result.iterations[0]
    assert outcome.decision_record.outcome == DecisionOutcome.KEEP_CANDIDATE


def test_loop_requires_cases_and_graders() -> None:
    exp = _experiment()
    executor = Executor(
        adapter=_adapter_for("pong", level=0.0),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    with pytest.raises(ValueError):
        OptimizationLoop(
            experiment=exp,
            executor=executor,
            proposer=ManualProposer([{"prompt": "x", "hypothesis": "y"}]),
            graders=[],
            cases=[_case()],
        )
    with pytest.raises(ValueError):
        OptimizationLoop(
            experiment=exp,
            executor=executor,
            proposer=ManualProposer([{"prompt": "x", "hypothesis": "y"}]),
            graders=[DeterministicGrader()],
            cases=[],
        )


@pytest.mark.asyncio
async def test_loop_excludes_holdout_from_optimization_set() -> None:
    opt_cases = [_case("pong") for _ in range(3)]
    held = [_case("pong", holdout=True) for _ in range(2)]
    exp = _experiment(max_iterations=1, search_space={"level": [1.0]})
    executor = Executor(
        adapter=_adapter_for("pong", level=1.0),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    loop = OptimizationLoop(
        experiment=exp,
        executor=executor,
        proposer=GridProposer(),
        graders=[DeterministicGrader()],
        cases=[*opt_cases, *held],
    )
    assert {c.id for c in loop.optimization_cases} == {c.id for c in opt_cases}
    assert {c.id for c in loop.holdout_cases} == {c.id for c in held}
    result = await loop.run()
    # Only the 3 non-holdout cases were evaluated.
    assert result.iterations[0].iteration_record.execution.ran_against == {"case_count": 3}
    assert all(len(run.repetitions) >= 1 for run in result.iterations[0].case_runs)
    assert {run.case_id for run in result.iterations[0].case_runs} == {c.id for c in opt_cases}


def test_loop_rejects_all_holdout_cases() -> None:
    exp = _experiment(max_iterations=1, search_space={"level": [1.0]})
    executor = Executor(
        adapter=_adapter_for("pong", level=1.0),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    with pytest.raises(ValueError, match="empty optimization set"):
        OptimizationLoop(
            experiment=exp,
            executor=executor,
            proposer=GridProposer(),
            graders=[DeterministicGrader()],
            cases=[_case("pong", holdout=True)],
        )


def test_loop_rejects_invalid_grade_concurrency() -> None:
    exp = _experiment()
    executor = Executor(
        adapter=_adapter_for("pong", level=1.0),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    with pytest.raises(ValueError):
        OptimizationLoop(
            experiment=exp,
            executor=executor,
            proposer=GridProposer(),
            graders=[DeterministicGrader()],
            cases=[_case()],
            grade_concurrency=0,
        )


@pytest.mark.asyncio
async def test_loop_warns_when_max_iterations_truncates_grid(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # 4 combos (2x2) but max_iterations=2 → loop should warn and still finish.
    cases = [_case("pong")]
    exp = _experiment(
        max_iterations=2,
        search_space={"level": [0.0, 1.0], "top_p": [0.9, 1.0]},
    )
    executor = Executor(
        adapter=_adapter_for("pong", level=1.0),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    loop = OptimizationLoop(
        experiment=exp,
        executor=executor,
        proposer=GridProposer(),
        graders=[DeterministicGrader()],
        cases=cases,
    )
    with caplog.at_level(logging.WARNING, logger="selfevals.optimization"):
        result = await loop.run()
    # WARN + CONTINUE: the run completes, only the first 2 of 4 combos ran.
    assert exp.state == ExperimentState.COMPLETED
    assert len(result.iterations) == 2
    warnings = [
        r
        for r in caplog.records
        if r.name == "selfevals.optimization" and r.levelno == logging.WARNING
    ]
    assert any("will be skipped" in r.getMessage() for r in warnings)
    assert any("max_iterations=2" in r.getMessage() for r in warnings)


@pytest.mark.asyncio
async def test_loop_no_warning_when_max_iterations_covers_grid(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cases = [_case("pong")]
    # 2 combos, max_iterations=4 → covers the whole grid, no truncation warning.
    exp = _experiment(max_iterations=4, search_space={"level": [0.0, 1.0]})
    executor = Executor(
        adapter=_adapter_for("pong", level=1.0),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    loop = OptimizationLoop(
        experiment=exp,
        executor=executor,
        proposer=GridProposer(),
        graders=[DeterministicGrader()],
        cases=cases,
    )
    with caplog.at_level(logging.WARNING, logger="selfevals.optimization"):
        await loop.run()
    assert not any("will be skipped" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_loop_grades_concurrently_and_preserves_order() -> None:
    import asyncio

    from selfevals.graders.base import GradeLabel, Grader, GraderContext, GradeResult
    from selfevals.optimization.proposers import ProposerContext

    barrier = {"count": 0, "max": 0}

    class _SlowGrader(Grader):
        def __init__(self, name: str) -> None:
            self.name = name

        async def grade(self, context: GraderContext) -> GradeResult:
            barrier["count"] += 1
            barrier["max"] = max(barrier["max"], barrier["count"])
            await asyncio.sleep(0.05)
            barrier["count"] -= 1
            return GradeResult(grader=self.name, label=GradeLabel.PASS, reason="ok", score=1.0)

    graders = [_SlowGrader(f"g{i}") for i in range(3)]
    # 2 reps * 3 graders = 6 grade tasks; with concurrency 8 they should overlap.
    exp = _experiment(max_iterations=1, search_space={"level": [1.0]})
    executor = Executor(
        adapter=_adapter_for("pong", level=1.0),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    loop = OptimizationLoop(
        experiment=exp,
        executor=executor,
        proposer=GridProposer(),
        graders=graders,  # type: ignore[arg-type]
        cases=[_case("pong")],
        repetitions_per_case=2,
        grade_concurrency=8,
    )
    proposal = loop._proposer.propose(exp, ProposerContext(iteration_index=0, history=()))
    _, _, per_case, _persisted = await loop._run_iteration(proposal, iteration=0)
    # More than one grade task ran at the same time → concurrency is real.
    assert barrier["max"] > 1
    # Order preserved: grades within each rep are in grader order g0,g1,g2.
    grades_per_rep = next(iter(per_case.values()))
    assert len(grades_per_rep) == 2
    for grades in grades_per_rep:
        assert [g.grader for g in grades] == ["g0", "g1", "g2"]


def test_loop_rejects_invalid_case_concurrency() -> None:
    exp = _experiment()
    executor = Executor(
        adapter=_adapter_for("pong", level=1.0),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    with pytest.raises(ValueError):
        OptimizationLoop(
            experiment=exp,
            executor=executor,
            proposer=GridProposer(),
            graders=[DeterministicGrader()],
            cases=[_case()],
            case_concurrency=0,
        )


@pytest.mark.asyncio
async def test_loop_runs_cases_concurrently_and_preserves_order() -> None:
    # Regression guard for the case fan-out fix: before it, the loop ran cases
    # strictly in series (`for case in self._cases: await ...`), so N slow cases
    # took N * latency regardless of `parallelism`. Now cases run concurrently
    # under the case semaphore, while case order is preserved downstream.
    from selfevals.optimization.proposers import ProposerContext

    barrier = {"count": 0, "max": 0}

    def _slow_fn(req: AdapterRequest) -> AdapterResponse:
        # Adapter calls are sync; the executor runs them via to_thread, so the
        # only way `max` exceeds 1 is if multiple cases are in flight at once.
        import time

        barrier["count"] += 1
        barrier["max"] = max(barrier["max"], barrier["count"])
        time.sleep(0.05)
        barrier["count"] -= 1
        return AdapterResponse(content="pong", tokens_input=4, tokens_output=2)

    adapter = EmbeddedAdapter(_slow_fn, agent=_agent())
    # 8 cases, each tagged with its index so we can assert the output order.
    cases = [_case("pong") for _ in range(8)]
    exp = _experiment(max_iterations=1, search_space={"level": [1.0]})
    executor = Executor(
        adapter=adapter,
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    loop = OptimizationLoop(
        experiment=exp,
        executor=executor,
        proposer=GridProposer(),
        graders=[DeterministicGrader()],
        cases=cases,
        case_concurrency=8,
    )
    proposal = loop._proposer.propose(exp, ProposerContext(iteration_index=0, history=()))
    aggregate, case_runs, _per_case, _persisted = await loop._run_iteration(proposal, iteration=0)
    # More than one case ran at the same time → inter-case concurrency is real.
    assert barrier["max"] > 1
    # gather preserves input order: case_runs line up with the input cases.
    assert [cr.case_id for cr in case_runs] == [c.id for c in cases]
    assert [o.case_id for o in aggregate.case_outcomes] == [c.id for c in cases]
