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

from bootstrap.analysis.staging import AnalysisStagingRecord
from bootstrap.graders.deterministic import DeterministicGrader
from bootstrap.optimization.loop import OptimizationLoop, _dominant_modes
from bootstrap.optimization.proposers import GridProposer
from bootstrap.runner.adapters import AdapterRequest, AdapterResponse, EmbeddedAdapter
from bootstrap.runner.executor import Executor
from bootstrap.runner.sandbox import SandboxPolicy
from bootstrap.schemas._base import EntityRef
from bootstrap.schemas.enums import (
    AgentType,
    DatasetSource,
    DatasetType,
    GroundTruthMethod,
    Level,
    Mode,
    ProposerStrategy,
    SandboxMode,
)
from bootstrap.schemas.eval_case import (
    CaseTaxonomy,
    EvalCase,
    Expected,
    FeatureTag,
    GroundTruthSpec,
    SourceInfo,
)
from bootstrap.schemas.experiment import (
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
from bootstrap.schemas.fleet import Agent, ModelRef
from bootstrap.schemas.workspace import Workspace
from bootstrap.storage.sqlite import SQLiteStorage

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


def _scoped_loop(exp: Experiment, adapter: EmbeddedAdapter, tmp_path: Path) -> tuple[
    OptimizationLoop, SQLiteStorage
]:
    storage = SQLiteStorage(tmp_path / "db.sqlite")
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


def test_stages_analysis_when_fail_rate_clears_threshold(tmp_path: Path) -> None:
    exp = _experiment(
        max_iterations=1,
        search_space={"level": [0.0]},
        error_analysis=ErrorAnalysisSpec(
            enabled=True, trigger=AnalysisTriggerSpec(threshold=0.10)
        ),
    )
    loop, storage = _scoped_loop(exp, _failing_adapter(), tmp_path)
    loop.run()
    with storage.open(WS) as s:
        staged = [e for e in s.list_entities(AnalysisStagingRecord)]
    storage.close()
    assert len(staged) == 1
    rec = staged[0]
    assert isinstance(rec, AnalysisStagingRecord)
    assert rec.experiment_id == exp.id
    assert rec.fail_rate == 1.0
    assert rec.scope == "failed_only"


def test_does_not_stage_when_disabled(tmp_path: Path) -> None:
    exp = _experiment(max_iterations=1, search_space={"level": [0.0]})  # disabled default
    loop, storage = _scoped_loop(exp, _failing_adapter(), tmp_path)
    loop.run()
    with storage.open(WS) as s:
        staged = list(s.list_entities(AnalysisStagingRecord))
    storage.close()
    assert staged == []


def test_does_not_stage_when_run_is_healthy(tmp_path: Path) -> None:
    exp = _experiment(
        max_iterations=1,
        search_space={"level": [1.0]},
        error_analysis=ErrorAnalysisSpec(
            enabled=True, trigger=AnalysisTriggerSpec(threshold=0.10)
        ),
    )
    loop, storage = _scoped_loop(exp, _passing_adapter(), tmp_path)
    loop.run()
    with storage.open(WS) as s:
        staged = list(s.list_entities(AnalysisStagingRecord))
    storage.close()
    assert staged == []


def test_failure_modes_consulted_carries_prior_iteration(tmp_path: Path) -> None:
    # Two failing iterations: iteration 1 should be shown iteration 0's modes.
    exp = _experiment(max_iterations=2, search_space={"level": [0.0, 0.0]})
    loop, storage = _scoped_loop(exp, _failing_adapter(), tmp_path)
    result = loop.run()
    storage.close()
    assert len(result.iterations) == 2
    first, second = result.iterations
    # Iteration 0 had no prior context.
    assert first.iteration_record.proposer.failure_modes_consulted == []
    # Iteration 1 was shown iteration 0's dominant mode.
    assert second.iteration_record.proposer.failure_modes_consulted == [
        "missing_required_substring"
    ]
