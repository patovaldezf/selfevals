"""End-to-end: OptimizationLoop + DecisionMatrixEvaluator together."""

from __future__ import annotations

from typing import Any

from selfevals.decision.matrix import DecisionMatrixEvaluator
from selfevals.graders.deterministic import DeterministicGrader
from selfevals.optimization.loop import OptimizationLoop
from selfevals.optimization.proposers import GridProposer
from selfevals.runner.adapters import AdapterRequest, AdapterResponse, EmbeddedAdapter
from selfevals.runner.executor import Executor
from selfevals.runner.sandbox import SandboxPolicy
from selfevals.schemas._base import EntityRef
from selfevals.schemas.enums import (
    AgentType,
    DatasetSource,
    DatasetType,
    DecisionOutcome,
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
    RunSpec,
    SearchSpace,
    TargetSpec,
)
from selfevals.schemas.fleet import Agent, ModelRef

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


def _experiment(*, max_iter: int = 3, search_space: dict[str, Any] | None = None) -> Experiment:
    return Experiment(
        id=Experiment.make_id(),
        workspace_id=WS,
        name="x",
        goal="x",
        mode=Mode.HANDOFF,
        taxonomy=ExperimentTaxonomy(
            target_features=["commerce.product_resolution"],
            dataset_types=[DatasetType.CAPABILITY],
        ),
        datasets=DatasetUsage(optimization=EntityRef(id="ds_x", version=1)),
        target=TargetSpec(primary=MetricTarget(name="pass@1", operator=">=", value=0.5)),
        editable=EditableContract(prompt=True, model_params=True),
        frozen=FrozenSnapshot(
            fleet=EntityRef(id="flt_x"),
            agents=[EntityRef(id="ag_x")],
            datasets=[EntityRef(id="ds_y")],
        ),
        proposer=ProposerSpec(strategy=ProposerStrategy.GRID),
        run=RunSpec(
            sandbox=SandboxMode.MOCK,
            max_iterations=max_iter,
            convergence=ConvergenceSpec(min_delta=1e-6, patience=10),
        ),
        search_space=SearchSpace(model_params=search_space or {}),
    )


def _staged_adapter() -> EmbeddedAdapter:
    """Adapter that returns 'pong' iff model_params.level >= 1."""

    def fn(req: AdapterRequest) -> AdapterResponse:
        level = req.parameters.get("model_params", {}).get("level", 0)
        return AdapterResponse(content="pong" if level >= 1 else "miss")

    return EmbeddedAdapter(fn, agent=_agent())


def test_decision_record_outcome_reflects_improvement_or_reject() -> None:
    exp = _experiment(max_iter=3, search_space={"level": [0, 1, 0]})
    executor = Executor(
        adapter=_staged_adapter(),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    loop = OptimizationLoop(
        experiment=exp,
        executor=executor,
        proposer=GridProposer(),
        graders=[DeterministicGrader()],
        cases=[_case()],
        decision_evaluator=DecisionMatrixEvaluator(),
    )
    result = loop.run()
    outcomes = [it.decision_record.outcome for it in result.iterations]
    # iter 0: level=0 → primary=0 → below target → INVESTIGATE (first iter).
    # iter 1: level=1 → primary=1 → improvement → KEEP_CANDIDATE.
    # iter 2: level=0 → primary=0 → regression below target → REJECT (per policy).
    assert outcomes == [
        DecisionOutcome.INVESTIGATE,
        DecisionOutcome.KEEP_CANDIDATE,
        DecisionOutcome.REJECT,
    ]
