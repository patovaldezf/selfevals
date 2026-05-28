"""Funnel rendering in markdown.py and compare.py."""

from __future__ import annotations

from selfevals.graders.base import BreakdownNode, GradeLabel
from selfevals.optimization.aggregator import FunnelNode, IterationAggregate, aggregate_iteration
from selfevals.optimization.loop import IterationOutcome, OptimizationResult
from selfevals.reporter import render_markdown
from selfevals.reporter.compare import render_compare
from selfevals.schemas._base import EntityRef
from selfevals.schemas.enums import (
    DatasetType,
    DecisionOutcome,
    IterationState,
    Mode,
    ProposerStrategy,
    SandboxMode,
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
from selfevals.schemas.iteration import (
    DecisionRationale,
    DecisionRecord,
    ExecutionInfo,
    IterationDecision,
    IterationMetrics,
    IterationRecord,
    MetricObservation,
    Proposal,
    ProposerInputs,
)

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _experiment() -> Experiment:
    return Experiment(
        id=Experiment.make_id(),
        workspace_id=WS,
        name="x",
        goal="g",
        mode=Mode.HANDOFF,
        taxonomy=ExperimentTaxonomy(
            target_features=["a"],
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
            max_iterations=3,
            convergence=ConvergenceSpec(min_delta=1e-6, patience=10),
        ),
        search_space=SearchSpace(model_params={"x": [0.0, 1.0]}),
        reliability=ReliabilitySpec(metrics=["pass@1"]),
    )


def _iteration(
    *,
    iteration: int = 0,
    primary: float = 0.5,
    aggregate: IterationAggregate | None = None,
    funnel_metrics: dict[str, object] | None = None,
) -> IterationOutcome:
    agg = aggregate or IterationAggregate(
        primary_metric="pass@1",
        primary_value=primary,
        case_count=1,
    )
    proposal = Proposal(parameters={"x": 1}, hypothesis="h")
    record = IterationRecord(
        id=IterationRecord.make_id(),
        workspace_id=WS,
        experiment_id="exp_x",
        iteration=iteration,
        state=IterationState.COMPLETED,
        proposer=ProposerInputs(type=ProposerStrategy.MANUAL),
        hypothesis="h",
        proposed_parameters={"x": 1},
        execution=ExecutionInfo(variant_id="var_x"),
        metrics=IterationMetrics(
            primary=MetricObservation(name="pass@1", value=primary),
            funnel=funnel_metrics or {},
        ),
        decision=IterationDecision(outcome=DecisionOutcome.KEEP_CANDIDATE, rationale="r"),
    )
    decision_record = DecisionRecord(
        id=DecisionRecord.make_id(),
        workspace_id=WS,
        experiment_id="exp_x",
        iteration=iteration,
        variant_id="var_x",
        outcome=DecisionOutcome.KEEP_CANDIDATE,
        rationale=DecisionRationale(automated="r"),
    )
    return IterationOutcome(
        iteration=iteration,
        proposal=proposal,
        aggregate=agg,
        case_runs=[],
        iteration_record=record,
        decision_record=decision_record,
    )


def _result(*iters: IterationOutcome) -> OptimizationResult:
    r = OptimizationResult(experiment=_experiment())
    r.iterations.extend(iters)
    return r


def _funnel_aggregate() -> IterationAggregate:
    from selfevals.optimization.aggregator import CaseOutcome

    outcome = CaseOutcome(
        case_id="ec_1",
        per_repetition_label=[GradeLabel.PARTIAL],
        per_repetition_score=[0.5],
        breakdowns=[
            BreakdownNode(
                key="overall",
                score=0.5,
                weight=1.0,
                children=[
                    BreakdownNode(key="retrieval", score=1.0, weight=1.0),
                    BreakdownNode(
                        key="answer",
                        score=0.0,
                        weight=1.0,
                        failure_modes=["wrong_answer"],
                    ),
                ],
            )
        ],
    )
    return aggregate_iteration(case_outcomes=[outcome])


# --- markdown ---------------------------------------------------------------


def test_markdown_funnel_section_rendered_when_present() -> None:
    result = _result(_iteration(primary=1.0, aggregate=_funnel_aggregate()))
    md = render_markdown(result)
    assert "## Funnel" in md
    assert "`overall`" in md
    assert "`retrieval`" in md
    assert "`answer`" in md
    assert "wrong_answer x1" in md
    # nesting: children are indented under the root
    assert "  - `retrieval`" in md


def test_markdown_funnel_section_omitted_when_no_data() -> None:
    result = _result(_iteration(primary=1.0))
    md = render_markdown(result)
    assert "## Funnel" not in md


# --- compare ----------------------------------------------------------------


def test_compare_funnel_diff_shown_when_either_side_has_funnel() -> None:
    a_funnel = {key: node.to_dict() for key, node in _funnel_aggregate().funnel.items()}
    # B improves the answer sub-node from 0.0 to 1.0.
    from selfevals.optimization.aggregator import CaseOutcome

    b_outcome = CaseOutcome(
        case_id="ec_1",
        per_repetition_label=[GradeLabel.PASS],
        per_repetition_score=[1.0],
        breakdowns=[
            BreakdownNode(
                key="overall",
                score=1.0,
                weight=1.0,
                children=[
                    BreakdownNode(key="retrieval", score=1.0, weight=1.0),
                    BreakdownNode(key="answer", score=1.0, weight=1.0),
                ],
            )
        ],
    )
    b_funnel = {
        key: node.to_dict()
        for key, node in aggregate_iteration(case_outcomes=[b_outcome]).funnel.items()
    }
    a = _iteration(iteration=0, primary=0.5, funnel_metrics=a_funnel).iteration_record
    b = _iteration(iteration=1, primary=1.0, funnel_metrics=b_funnel).iteration_record
    out = render_compare(a, b)
    assert "## Funnel diff" in out
    assert "`overall`" in out
    assert "`overall.answer`" in out
    # answer improved 0.0 -> 1.0
    assert "+1" in out


def test_compare_funnel_diff_omitted_when_neither_has_funnel() -> None:
    a = _iteration(iteration=0, primary=0.5).iteration_record
    b = _iteration(iteration=1, primary=0.6).iteration_record
    out = render_compare(a, b)
    assert "## Funnel diff" not in out


def test_funnel_node_default_construction() -> None:
    node = FunnelNode(key="k")
    assert node.count == 0
    assert node.mean_score is None
    assert node.children == {}
