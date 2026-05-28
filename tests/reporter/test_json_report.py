"""Unit tests for reporter.json_report — the stable JSON surface.

Focus: grader failure reasons are exposed per iteration (Issue B). The loop
stamps each grade's free-text rationale onto the persisted trace's
`grader_results`; the JSON report surfaces a deduplicated `failure_reasons`
list so a consumer can see WHY a grader failed.
"""

from __future__ import annotations

from datetime import UTC, datetime

from selfevals.optimization.aggregator import IterationAggregate
from selfevals.optimization.loop import IterationOutcome, OptimizationResult
from selfevals.reporter.json_report import to_dict
from selfevals.runner.executor import CaseRun, RepetitionResult
from selfevals.schemas._base import EntityRef
from selfevals.schemas.enums import (
    DatasetType,
    DecisionOutcome,
    IterationState,
    Mode,
    ProposerStrategy,
    SandboxMode,
    TraceState,
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
from selfevals.schemas.trace import (
    AgentSnapshotRef,
    EnvironmentInfo,
    FinalState,
    GraderResult,
    RunInfo,
    Trace,
)

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"
T0 = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)


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


def _trace(run_id: str, *grader_results: GraderResult) -> Trace:
    return Trace(
        id=Trace.make_id(),
        workspace_id=WS,
        run=RunInfo(run_id=run_id),
        agent=AgentSnapshotRef(agent_id="ag_x", agent_version=1),
        environment=EnvironmentInfo(
            framework_version="selfevals/0.0.1",
            runtime="python-3.12",
            sandbox=SandboxMode.MOCK,
            started_at=T0,
        ),
        final_state=FinalState(status=TraceState.COMPLETED),
        grader_results=list(grader_results),
    )


def _iteration(*case_runs: CaseRun) -> IterationOutcome:
    aggregate = IterationAggregate(
        primary_metric="pass@1",
        primary_value=0.5,
        total_cost_usd=0.0,
        total_duration_ms=0,
        case_count=len(case_runs) or 1,
    )
    proposal = Proposal(parameters={"x": 1}, hypothesis="h")
    record = IterationRecord(
        id=IterationRecord.make_id(),
        workspace_id=WS,
        experiment_id="exp_x",
        iteration=0,
        state=IterationState.COMPLETED,
        proposer=ProposerInputs(type=ProposerStrategy.MANUAL),
        hypothesis="h",
        proposed_parameters={"x": 1},
        execution=ExecutionInfo(variant_id="var_x"),
        metrics=IterationMetrics(primary=MetricObservation(name="pass@1", value=0.5)),
        decision=IterationDecision(outcome=DecisionOutcome.KEEP_CANDIDATE, rationale="r"),
    )
    decision_record = DecisionRecord(
        id=DecisionRecord.make_id(),
        workspace_id=WS,
        experiment_id="exp_x",
        iteration=0,
        variant_id="var_x",
        outcome=DecisionOutcome.KEEP_CANDIDATE,
        rationale=DecisionRationale(automated="r"),
    )
    return IterationOutcome(
        iteration=0,
        proposal=proposal,
        aggregate=aggregate,
        case_runs=list(case_runs),
        iteration_record=record,
        decision_record=decision_record,
    )


def _result(it: IterationOutcome) -> OptimizationResult:
    r = OptimizationResult(experiment=_experiment())
    r.iterations.append(it)
    return r


def test_failure_reasons_surface_in_iteration() -> None:
    gr = GraderResult(
        grader="exact_match",
        label="fail",
        score=0.0,
        reason="expected 'pong' but got 'miss'",
        failure_modes=["wrong_answer"],
    )
    case_run = CaseRun(
        case_id="c1",
        repetitions=[
            RepetitionResult(repetition=0, trace=_trace("r0", gr), response=None, error=None)
        ],
    )
    out = to_dict(_result(_iteration(case_run)))
    reasons = out["iterations"][0]["failure_reasons"]
    assert reasons == [
        {
            "grader": "exact_match",
            "label": "fail",
            "score": 0.0,
            "reason": "expected 'pong' but got 'miss'",
            "failure_modes": ["wrong_answer"],
        }
    ]


def test_failure_reasons_deduplicated_across_reps() -> None:
    gr = GraderResult(grader="exact_match", label="fail", reason="boom", failure_modes=[])
    reps = [
        RepetitionResult(repetition=i, trace=_trace(f"r{i}", gr), response=None, error=None)
        for i in range(3)
    ]
    case_run = CaseRun(case_id="c1", repetitions=reps)
    out = to_dict(_result(_iteration(case_run)))
    reasons = out["iterations"][0]["failure_reasons"]
    assert len(reasons) == 1
    assert reasons[0]["reason"] == "boom"


def test_failure_reasons_excludes_passing_grades() -> None:
    passed = GraderResult(grader="exact_match", label="pass", reason="all good")
    case_run = CaseRun(
        case_id="c1",
        repetitions=[
            RepetitionResult(repetition=0, trace=_trace("r0", passed), response=None, error=None)
        ],
    )
    out = to_dict(_result(_iteration(case_run)))
    assert out["iterations"][0]["failure_reasons"] == []


def test_failure_reasons_empty_when_no_grader_results() -> None:
    case_run = CaseRun(
        case_id="c1",
        repetitions=[RepetitionResult(repetition=0, trace=_trace("r0"), response=None, error=None)],
    )
    out = to_dict(_result(_iteration(case_run)))
    assert out["iterations"][0]["failure_reasons"] == []
