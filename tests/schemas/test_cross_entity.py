"""Cross-entity sanity: building a full Experiment + Proposal + IterationRecord +
Trace + DecisionRecord chain end-to-end at the schema layer.

This is the closest we get to a contract integration test before storage
(PR 2) and runner (PR 4) exist.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from selfevals.schemas import (
    Agent,
    AgentFleet,
    AgentSnapshotRef,
    AgentTurnSpan,
    CodeDiff,
    Dataset,
    DatasetUsage,
    DecisionRationale,
    DecisionRecord,
    EditableContract,
    EntityRef,
    EnvironmentInfo,
    ExecutionInfo,
    Expected,
    Experiment,
    ExperimentTaxonomy,
    FeatureRegistry,
    FeatureTag,
    FinalState,
    FrozenSnapshot,
    GroundTruthSpec,
    IterationDecision,
    IterationMetrics,
    IterationRecord,
    LLMCallSpan,
    LLMOutput,
    MetricObservation,
    MetricTarget,
    ModelRef,
    Proposal,
    ProposalRejectedError,
    ProposerInputs,
    ProposerSpec,
    RiskProfile,
    RunInfo,
    RunSpec,
    SourceInfo,
    TargetSpec,
    ToolCallSpan,
    ToolUseRequest,
    Trace,
    Workspace,
)
from selfevals.schemas.enums import (
    AgentType,
    DatasetSource,
    DatasetStatus,
    DatasetType,
    DecisionOutcome,
    FeatureKind,
    GroundTruthMethod,
    IterationState,
    Level,
    Mode,
    ProposerStrategy,
    SandboxMode,
    StopReason,
    ToolCallStatus,
    TraceState,
)
from selfevals.schemas.eval_case import CaseTaxonomy, EvalCase


def _make_workspace() -> Workspace:
    ws_id = Workspace.make_id()
    return Workspace(id=ws_id, workspace_id=ws_id, slug="pato", name="Pato workspace")


def _make_feature(ws: Workspace) -> FeatureRegistry:
    return FeatureRegistry(
        id=FeatureRegistry.make_id(),
        workspace_id=ws.id,
        kind=FeatureKind.PRODUCT_FEATURE,
        primary_feature="commerce.product_resolution",
        description="Resolve a customer mention to a SKU.",
        default_risk=RiskProfile(overall="medium"),
    )


def _make_agent_and_fleet(ws: Workspace) -> tuple[Agent, AgentFleet]:
    agent = Agent(
        id=Agent.make_id(),
        workspace_id=ws.id,
        agent_type=AgentType.SYSTEM_PROMPT,
        model=ModelRef(provider="anthropic", name="claude-sonnet-4-6"),
        system_prompt_pointer="oss://prompts/acme-resolver-v1",
        tools=["tl_search"],
        features=["commerce.product_resolution"],
    )
    fleet = AgentFleet(
        id=AgentFleet.make_id(),
        workspace_id=ws.id,
        name="acme-prod",
        agents=[EntityRef(id=agent.id, version=agent.version)],
        features=["commerce.product_resolution"],
    )
    return agent, fleet


def _make_dataset_and_case(ws: Workspace) -> tuple[Dataset, EvalCase]:
    case = EvalCase(
        id=EvalCase.make_id(),
        workspace_id=ws.id,
        name="manzanas-rojas",
        task_type="product_resolution",
        input={"messages": [{"role": "user", "content": "necesito manzanas rojas"}]},
        taxonomy=CaseTaxonomy(
            level=Level.FINAL_RESPONSE,
            feature=FeatureTag(primary="commerce.product_resolution"),
            source=SourceInfo(type=DatasetSource.HANDCRAFTED),
            ground_truth=GroundTruthSpec(methods=[GroundTruthMethod.EXACT_MATCH]),
            dataset_type=DatasetType.CAPABILITY,
        ),
        expected=Expected(must_include=["apple"]),
    )
    dataset = Dataset(
        id=Dataset.make_id(),
        workspace_id=ws.id,
        name="capability-product-resolution",
        dataset_type=DatasetType.CAPABILITY,
        cases=[EntityRef(id=case.id, version=case.version)],
        manifest_hash="sha256:deadbeef",
        status=DatasetStatus.ACTIVE,
    )
    return dataset, case


def _make_experiment(
    ws: Workspace, *, fleet: AgentFleet, agent: Agent, dataset: Dataset
) -> Experiment:
    return Experiment(
        id=Experiment.make_id(),
        workspace_id=ws.id,
        name="raise-pass1",
        goal="Raise pass@1 above 0.85.",
        mode=Mode.HANDOFF,
        taxonomy=ExperimentTaxonomy(
            target_features=["commerce.product_resolution"],
            dataset_types=[DatasetType.CAPABILITY],
        ),
        datasets=DatasetUsage(optimization=EntityRef(id=dataset.id, version=dataset.version)),
        target=TargetSpec(primary=MetricTarget(name="pass@1", operator=">=", value=0.85)),
        editable=EditableContract(prompt=True),
        frozen=FrozenSnapshot(
            fleet=EntityRef(id=fleet.id, version=fleet.version),
            agents=[EntityRef(id=agent.id, version=agent.version)],
            datasets=[EntityRef(id=dataset.id, version=dataset.version)],
        ),
        proposer=ProposerSpec(strategy=ProposerStrategy.GRID),
        run=RunSpec(sandbox=SandboxMode.DRY_RUN),
    )


def _make_trace(ws: Workspace, *, exp: Experiment, agent: Agent, case: EvalCase) -> Trace:
    started = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
    return Trace(
        id=Trace.make_id(),
        workspace_id=ws.id,
        run=RunInfo(
            run_id="run_001",
            experiment_id=exp.id,
            iteration=1,
            variant_id="v1",
            eval_case_id=case.id,
        ),
        agent=AgentSnapshotRef(agent_id=agent.id, agent_version=agent.version),
        environment=EnvironmentInfo(
            framework_version="selfevals/0.0.1",
            runtime="python-3.12",
            sandbox=SandboxMode.DRY_RUN,
            started_at=started,
        ),
        final_state=FinalState(status=TraceState.COMPLETED),
        spans=[
            AgentTurnSpan(id="sp_0", name="turn", started_at=started),
            LLMCallSpan(
                id="sp_1",
                parent_id="sp_0",
                name="resolve-product",
                started_at=started,
                provider="anthropic",
                model="claude-sonnet-4-6",
                output=LLMOutput(
                    stop_reason=StopReason.TOOL_USE,
                    tool_use_requested=[ToolUseRequest(tool="search", tool_use_id="toolu_01")],
                ),
            ),
            ToolCallSpan(
                id="sp_2",
                parent_id="sp_1",
                name="search",
                started_at=started,
                tool_name="search",
                tool_use_id="toolu_01",
                status=ToolCallStatus.OK,
            ),
        ],
    )


def _make_iteration_and_decision(
    ws: Workspace, *, exp: Experiment, trace: Trace, hypothesis: str | None
) -> tuple[IterationRecord, DecisionRecord]:
    itr = IterationRecord(
        id=IterationRecord.make_id(),
        workspace_id=ws.id,
        experiment_id=exp.id,
        iteration=1,
        state=IterationState.COMPLETED,
        proposer=ProposerInputs(type=ProposerStrategy.GRID),
        hypothesis=hypothesis,
        execution=ExecutionInfo(variant_id="v1", trace_run_ids=[trace.run.run_id]),
        metrics=IterationMetrics(
            primary=MetricObservation(name="pass@1", value=0.87, delta_vs_baseline=0.02),
        ),
        decision=IterationDecision(
            outcome=DecisionOutcome.KEEP_CANDIDATE,
            rationale="primary above target, no guardrail regression",
        ),
    )
    decision_record = DecisionRecord(
        id=DecisionRecord.make_id(),
        workspace_id=ws.id,
        experiment_id=exp.id,
        iteration=itr.iteration,
        variant_id="v1",
        outcome=DecisionOutcome.KEEP_CANDIDATE,
        rationale=DecisionRationale(automated="pass@1=0.87 >= target 0.85, guardrails ok"),
        metrics_snapshot={"pass@1": 0.87},
        affected_artifacts=[],
    )
    return itr, decision_record


def test_end_to_end_chain_constructable() -> None:
    ws = _make_workspace()
    feature = _make_feature(ws)
    assert feature.primary_feature == "commerce.product_resolution"

    agent, fleet = _make_agent_and_fleet(ws)
    dataset, case = _make_dataset_and_case(ws)
    exp = _make_experiment(ws, fleet=fleet, agent=agent, dataset=dataset)

    proposal = Proposal(
        parameters={"prompt": "improved prompt v2"},
        hypothesis="Adding few-shot examples raises recall.",
    )
    proposal.validate_against(exp)

    trace = _make_trace(ws, exp=exp, agent=agent, case=case)
    itr, decision_record = _make_iteration_and_decision(
        ws, exp=exp, trace=trace, hypothesis=proposal.hypothesis
    )
    decision_record = decision_record.model_copy(update={"affected_artifacts": [agent.id]})

    # Everything binds together by workspace_id; ids stay distinct.
    ids = [
        ws.id,
        feature.id,
        agent.id,
        fleet.id,
        case.id,
        dataset.id,
        exp.id,
        trace.id,
        itr.id,
        decision_record.id,
    ]
    assert len(set(ids)) == len(ids)
    for entity_id, ws_field in [
        (feature.workspace_id, ws.id),
        (agent.workspace_id, ws.id),
        (fleet.workspace_id, ws.id),
        (case.workspace_id, ws.id),
        (dataset.workspace_id, ws.id),
        (exp.workspace_id, ws.id),
        (trace.workspace_id, ws.id),
        (itr.workspace_id, ws.id),
        (decision_record.workspace_id, ws.id),
    ]:
        assert entity_id == ws_field
    assert decision_record.outcome == DecisionOutcome.KEEP_CANDIDATE


def test_code_diff_in_handoff_blocks_at_proposal_layer() -> None:
    """Cross-entity: handoff experiment must reject a Proposal carrying code_changes."""
    ws_id = Workspace.make_id()
    ws = Workspace(id=ws_id, workspace_id=ws_id, slug="pato", name="x")
    exp = Experiment(
        id=Experiment.make_id(),
        workspace_id=ws.id,
        name="x",
        goal="x",
        mode=Mode.HANDOFF,
        taxonomy=ExperimentTaxonomy(
            target_features=["commerce.product_resolution"],
            dataset_types=[DatasetType.CAPABILITY],
        ),
        datasets=DatasetUsage(optimization=EntityRef(id="ds_x", version=1)),
        target=TargetSpec(primary=MetricTarget(name="pass@1", operator=">=", value=0.85)),
        frozen=FrozenSnapshot(
            fleet=EntityRef(id="flt_x"),
            agents=[EntityRef(id="ag_x")],
            datasets=[EntityRef(id="ds_y")],
        ),
        proposer=ProposerSpec(strategy=ProposerStrategy.GRID),
        run=RunSpec(sandbox=SandboxMode.DRY_RUN),
    )
    proposal = Proposal(
        parameters={},
        hypothesis="refactor a tool",
        code_changes=[CodeDiff(path="tools/x.py", operation="modify")],
    )
    with pytest.raises(ProposalRejectedError):
        proposal.validate_against(exp)
