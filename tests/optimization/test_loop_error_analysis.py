"""Closing the loop (design §7, §9):

- when an experiment opts into error analysis and an iteration's fail rate
  clears the trigger, the loop persists an advisory `AnalysisStagingRecord`;
- when it's disabled or the run is healthy, nothing is staged;
- the proposer for iteration N+1 is shown iteration N's dominant failure modes
  via `ProposerInputs.failure_modes_consulted`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from selfevals.analysis.hypothesis import HypothesisRecord
from selfevals.analysis.staging import AnalysisStagingRecord
from selfevals.graders.deterministic import DeterministicGrader
from selfevals.optimization.loop import OptimizationLoop, _dominant_modes
from selfevals.optimization.proposers import GridProposer, LLMProposer
from selfevals.runner.adapters import AdapterRequest, AdapterResponse, EmbeddedAdapter
from selfevals.runner.executor import Executor
from selfevals.runner.sandbox import SandboxPolicy
from selfevals.schemas._base import EntityRef
from selfevals.schemas.enums import (
    AgentType,
    DatasetSource,
    DatasetType,
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
    AnalysisTriggerSpec,
    ConvergenceSpec,
    DatasetUsage,
    EditableContract,
    ErrorAnalysisSpec,
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
from selfevals.schemas.trace import Trace
from selfevals.schemas.workspace import Workspace
from selfevals.storage.sqlite import SQLiteStorage

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _case() -> EvalCase:
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
        expected=Expected(must_include=["pong"]),
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
    max_iterations: int,
    search_space: dict[str, Any],
    error_analysis: ErrorAnalysisSpec | None = None,
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
        proposer=ProposerSpec(strategy=ProposerStrategy.GRID),
        run=RunSpec(
            sandbox=SandboxMode.MOCK,
            max_iterations=max_iterations,
            convergence=ConvergenceSpec(min_delta=1e-6, patience=max_iterations + 1),
            persist_traces=persist_traces,  # type: ignore[arg-type]
        ),
        search_space=SearchSpace(model_params=search_space),
        reliability=ReliabilitySpec(metrics=["pass@1"]),
        error_analysis=error_analysis or ErrorAnalysisSpec(),
    )


def _failing_adapter() -> EmbeddedAdapter:
    """Always emits 'miss' → every case fails the must_include=['pong'] check,
    tagging `missing_required_substring`. fail_rate == 1.0."""

    def fn(_req: AdapterRequest) -> AdapterResponse:
        return AdapterResponse(content="miss", tokens_input=4, tokens_output=2)

    return EmbeddedAdapter(fn, agent=_agent())


def _passing_adapter() -> EmbeddedAdapter:
    def fn(_req: AdapterRequest) -> AdapterResponse:
        return AdapterResponse(content="pong", tokens_input=4, tokens_output=2)

    return EmbeddedAdapter(fn, agent=_agent())


def _scoped_loop(
    exp: Experiment, adapter: EmbeddedAdapter, tmp_path: Path, *, db_name: str = "db.sqlite"
) -> tuple[OptimizationLoop, SQLiteStorage]:
    storage = SQLiteStorage(tmp_path / db_name)
    with storage.open(WS) as scope:
        scope.put_entity(Workspace(id=WS, workspace_id=WS, slug="t", name="t"))
    executor = Executor(
        adapter=adapter, sandbox=SandboxPolicy(SandboxMode.MOCK), workspace_id=WS
    )
    loop = OptimizationLoop(
        experiment=exp,
        executor=executor,
        proposer=GridProposer(),
        graders=[DeterministicGrader()],
        cases=[_case()],
        scope=storage.open(WS),
    )
    return loop, storage


def test_dominant_modes_orders_by_count_then_id() -> None:
    assert _dominant_modes({"a": 1, "b": 3, "c": 3}) == ["b", "c", "a"]
    assert _dominant_modes({}) == []


@pytest.mark.asyncio
async def test_stages_analysis_when_fail_rate_clears_threshold(tmp_path: Path) -> None:
    exp = _experiment(
        max_iterations=1,
        search_space={"level": [0.0]},
        error_analysis=ErrorAnalysisSpec(
            enabled=True, trigger=AnalysisTriggerSpec(threshold=0.10)
        ),
    )
    loop, storage = _scoped_loop(exp, _failing_adapter(), tmp_path)
    await loop.run()
    with storage.open(WS) as s:
        staged = [e for e in s.list_entities(AnalysisStagingRecord)]
    storage.close()
    assert len(staged) == 1
    rec = staged[0]
    assert isinstance(rec, AnalysisStagingRecord)
    assert rec.experiment_id == exp.id
    assert rec.fail_rate == 1.0
    assert rec.scope == "failed_only"


@pytest.mark.asyncio
async def test_does_not_stage_when_disabled(tmp_path: Path) -> None:
    exp = _experiment(max_iterations=1, search_space={"level": [0.0]})  # disabled default
    loop, storage = _scoped_loop(exp, _failing_adapter(), tmp_path)
    await loop.run()
    with storage.open(WS) as s:
        staged = list(s.list_entities(AnalysisStagingRecord))
    storage.close()
    assert staged == []


@pytest.mark.asyncio
async def test_does_not_stage_when_run_is_healthy(tmp_path: Path) -> None:
    exp = _experiment(
        max_iterations=1,
        search_space={"level": [1.0]},
        error_analysis=ErrorAnalysisSpec(
            enabled=True, trigger=AnalysisTriggerSpec(threshold=0.10)
        ),
    )
    loop, storage = _scoped_loop(exp, _passing_adapter(), tmp_path)
    await loop.run()
    with storage.open(WS) as s:
        staged = list(s.list_entities(AnalysisStagingRecord))
    storage.close()
    assert staged == []


@pytest.mark.asyncio
async def test_failure_modes_consulted_carries_prior_iteration(tmp_path: Path) -> None:
    # Two failing iterations: iteration 1 should be shown iteration 0's modes.
    exp = _experiment(max_iterations=2, search_space={"level": [0.0, 0.0]})
    loop, storage = _scoped_loop(exp, _failing_adapter(), tmp_path)
    result = await loop.run()
    storage.close()
    assert len(result.iterations) == 2
    first, second = result.iterations
    # Iteration 0 had no prior context.
    assert first.iteration_record.proposer.failure_modes_consulted == []
    # Iteration 1 was shown iteration 0's dominant mode.
    assert second.iteration_record.proposer.failure_modes_consulted == [
        "missing_required_substring"
    ]


def _persisted_traces(storage: SQLiteStorage) -> list[Trace]:
    with storage.open(WS) as s:
        return [t for t in s.list_entities(Trace) if isinstance(t, Trace)]


@pytest.mark.asyncio
async def test_persist_traces_none_writes_no_traces(tmp_path: Path) -> None:
    exp = _experiment(max_iterations=1, search_space={"level": [0.0]}, persist_traces="none")
    loop, storage = _scoped_loop(exp, _failing_adapter(), tmp_path)
    await loop.run()
    traces = _persisted_traces(storage)
    storage.close()
    assert traces == []


@pytest.mark.asyncio
async def test_persist_traces_all_writes_every_trace(tmp_path: Path) -> None:
    # One passing iteration → its trace is still persisted under `all`.
    exp = _experiment(max_iterations=1, search_space={"level": [1.0]}, persist_traces="all")
    loop, storage = _scoped_loop(exp, _passing_adapter(), tmp_path)
    await loop.run()
    traces = _persisted_traces(storage)
    storage.close()
    assert len(traces) == 1
    # Persisted with its grader results, so analyze pull can classify it.
    assert traces[0].grader_results
    assert traces[0].run.experiment_id == exp.id
    assert traces[0].run.iteration == 0


@pytest.mark.asyncio
async def test_persist_traces_failed_keeps_only_failures(tmp_path: Path) -> None:
    # A failing run → the trace IS persisted (it failed) with its mode tag.
    failing = _experiment(max_iterations=1, search_space={"level": [0.0]})  # default "failed"
    loop, storage = _scoped_loop(failing, _failing_adapter(), tmp_path)
    await loop.run()
    traces = _persisted_traces(storage)
    assert len(traces) == 1
    assert "missing_required_substring" in traces[0].grader_results[0].failure_modes
    storage.close()

    # A passing run under the same default → nothing persisted.
    passing = _experiment(max_iterations=1, search_space={"level": [1.0]})
    loop2, storage2 = _scoped_loop(passing, _passing_adapter(), tmp_path, db_name="pass.sqlite")
    await loop2.run()
    traces2 = _persisted_traces(storage2)
    storage2.close()
    assert traces2 == []


@pytest.mark.asyncio
async def test_llm_proposer_offline_consumes_seeded_hypotheses(tmp_path: Path) -> None:
    # The loop reads HypothesisRecord seeds from storage, the offline
    # LLMProposer applies them in order, and the loop persists each as
    # consumed so it isn't replayed. After the single seed is consumed the
    # loop terminates on an exhausted search space.
    exp = _experiment(max_iterations=5, search_space={"level": [0.0]})
    exp.proposer = ProposerSpec(strategy=ProposerStrategy.LLM_PROPOSER)

    storage = SQLiteStorage(tmp_path / "llm.sqlite")
    with storage.open(WS) as scope:
        scope.put_entity(Workspace(id=WS, workspace_id=WS, slug="t", name="t"))
        scope.put_entity(
            HypothesisRecord(
                id=HypothesisRecord.make_id(),
                workspace_id=WS,
                experiment_id=exp.id,
                targets_mode_slug="missing_required_substring",
                statement="say pong explicitly",
                suggested_parameters={"prompt": "always answer pong"},
            )
        )
    executor = Executor(
        adapter=_passing_adapter(), sandbox=SandboxPolicy(SandboxMode.MOCK), workspace_id=WS
    )
    loop = OptimizationLoop(
        experiment=exp,
        executor=executor,
        proposer=LLMProposer(),
        graders=[DeterministicGrader()],
        cases=[_case()],
        scope=storage.open(WS),
    )
    result = await loop.run()

    assert len(result.iterations) == 1
    only = result.iterations[0]
    assert only.proposal.hypothesis == "say pong explicitly"
    assert only.proposal.parameters == {"prompt": "always answer pong"}
    assert result.terminated_reason.startswith("search_space_exhausted")

    with storage.open(WS) as scope:
        seeds = [
            h for h in scope.list_entities(HypothesisRecord) if isinstance(h, HypothesisRecord)
        ]
    storage.close()
    assert len(seeds) == 1
    assert seeds[0].consumed_by_iteration == 0
