from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from bootstrap.graders.deterministic import DeterministicGrader
from bootstrap.optimization.loop import OptimizationLoop
from bootstrap.optimization.proposers import GridProposer, ManualProposer
from bootstrap.runner.adapters import AdapterRequest, AdapterResponse, EmbeddedAdapter
from bootstrap.runner.executor import Executor
from bootstrap.runner.sandbox import SandboxPolicy
from bootstrap.schemas._base import EntityRef
from bootstrap.schemas.enums import (
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
from bootstrap.schemas.eval_case import (
    CaseTaxonomy,
    EvalCase,
    Expected,
    FeatureTag,
    GroundTruthSpec,
    SourceInfo,
)
from bootstrap.schemas.experiment import (
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
from bootstrap.schemas.fleet import Agent, ModelRef
from bootstrap.schemas.iteration import DecisionRecord, IterationRecord
from bootstrap.schemas.workspace import Workspace
from bootstrap.storage.sqlite import SQLiteStorage

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _case(content_target: str = "pong") -> EvalCase:
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
            convergence=convergence or ConvergenceSpec(min_delta=1e-6, patience=2),
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


def test_loop_runs_max_iterations_when_no_convergence(tmp_path: Path) -> None:
    cases = [_case("pong")]
    exp = _experiment(
        max_iterations=3,
        search_space={"level": [0.0, 0.5, 1.0]},
    )
    storage = SQLiteStorage(tmp_path / "db.sqlite")
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
    result = loop.run()
    assert exp.state == ExperimentState.COMPLETED
    assert len(result.iterations) == 3
    # Storage persisted IterationRecord + DecisionRecord per iteration.
    with storage.open(ws_id) as s:
        iters = s.list_entities(IterationRecord)
        decisions = s.list_entities(DecisionRecord)
    assert len(iters) == 3
    assert len(decisions) == 3
    storage.close()


def test_loop_terminates_on_search_space_exhausted() -> None:
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
    result = loop.run()
    assert len(result.iterations) == 2
    assert result.terminated_reason.startswith("search_space_exhausted")


def test_loop_converges_when_no_improvement_for_patience() -> None:
    cases = [_case("pong")]
    # All proposals score the same (level=1.0 always → all pass).
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
    result = loop.run()
    assert result.terminated_reason == "converged"
    # patience=2 means we need >= 3 iters to detect convergence.
    assert len(result.iterations) >= 3


def test_loop_best_iteration_tracks_highest_primary() -> None:
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
    result = loop.run()
    best = result.best_iteration
    assert best is not None
    assert best.aggregate.primary_value == 1.0  # the level>=0.5 iters all pass


def test_loop_persists_decision_record_with_outcome() -> None:
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
    result = loop.run()
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
