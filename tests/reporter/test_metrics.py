"""Unit tests for reporter._metrics — pure cost/time computation."""

from __future__ import annotations

from bootstrap.optimization.aggregator import IterationAggregate
from bootstrap.optimization.loop import IterationOutcome, OptimizationResult
from bootstrap.reporter._metrics import (
    compute_cost_time_summary,
    compute_total_cases,
    compute_total_cost,
    compute_total_time_seconds,
)
from bootstrap.schemas._base import EntityRef
from bootstrap.schemas.enums import (
    DatasetType,
    DecisionOutcome,
    IterationState,
    Mode,
    ProposerStrategy,
    SandboxMode,
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
from bootstrap.schemas.iteration import (
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
    primary: float = 0.5,
    cost: float = 0.0,
    duration_ms: int = 0,
    case_count: int = 1,
) -> IterationOutcome:
    aggregate = IterationAggregate(
        primary_metric="pass@1",
        primary_value=primary,
        total_cost_usd=cost,
        total_duration_ms=duration_ms,
        case_count=case_count,
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
        metrics=IterationMetrics(
            primary=MetricObservation(name="pass@1", value=primary),
        ),
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
        case_runs=[],
        iteration_record=record,
        decision_record=decision_record,
    )


def _result(*iters: IterationOutcome) -> OptimizationResult:
    r = OptimizationResult(experiment=_experiment())
    r.iterations.extend(iters)
    return r


# ----- compute_total_cost -----


def test_total_cost_returns_none_when_no_iterations() -> None:
    assert compute_total_cost(_result()) is None


def test_total_cost_returns_none_when_all_zero() -> None:
    # An echo agent run reports 0.0 for every iteration → we treat it
    # as "no data", not as "free" — callers omit the section.
    result = _result(
        _iteration(cost=0.0, case_count=2),
        _iteration(cost=0.0, case_count=2),
    )
    assert compute_total_cost(result) is None


def test_total_cost_sums_when_any_iteration_has_cost() -> None:
    result = _result(
        _iteration(cost=0.10, case_count=2),
        _iteration(cost=0.25, case_count=2),
    )
    assert compute_total_cost(result) == 0.35


def test_total_cost_partial_data_is_real() -> None:
    # One iter has cost, the other does not — the total is the real
    # number, not None. We treat zero-cost iters as having free data
    # (typical when a single iter cached).
    result = _result(
        _iteration(cost=0.10, case_count=2),
        _iteration(cost=0.0, case_count=2),
    )
    assert compute_total_cost(result) == 0.10


# ----- compute_total_time_seconds -----


def test_total_time_none_when_no_iterations() -> None:
    assert compute_total_time_seconds(_result()) is None


def test_total_time_none_when_all_zero() -> None:
    result = _result(_iteration(duration_ms=0), _iteration(duration_ms=0))
    assert compute_total_time_seconds(result) is None


def test_total_time_sums_in_seconds() -> None:
    result = _result(
        _iteration(duration_ms=1500),
        _iteration(duration_ms=500),
    )
    assert compute_total_time_seconds(result) == 2.0


# ----- compute_total_cases -----


def test_total_cases_sums_case_counts() -> None:
    result = _result(
        _iteration(case_count=3),
        _iteration(case_count=4),
    )
    assert compute_total_cases(result) == 7


def test_total_cases_zero_when_empty() -> None:
    assert compute_total_cases(_result()) == 0


# ----- compute_cost_time_summary -----


def test_summary_no_data() -> None:
    result = _result(_iteration(cost=0.0, duration_ms=0, case_count=2))
    summary = compute_cost_time_summary(result)
    assert summary.has_any is False
    assert summary.has_cost is False
    assert summary.has_time is False
    assert summary.cost_total_usd is None
    assert summary.time_total_seconds is None
    assert summary.iterations == 1
    assert summary.cases_run == 2


def test_summary_with_cost_only() -> None:
    result = _result(
        _iteration(cost=0.20, duration_ms=0, case_count=2),
        _iteration(cost=0.40, duration_ms=0, case_count=2),
    )
    summary = compute_cost_time_summary(result)
    assert summary.has_cost is True
    assert summary.has_time is False
    assert summary.cost_total_usd is not None
    assert abs(summary.cost_total_usd - 0.6) < 1e-9
    assert summary.cost_per_iteration_usd is not None
    assert abs(summary.cost_per_iteration_usd - 0.3) < 1e-9
    assert summary.cost_per_case_usd is not None
    assert abs(summary.cost_per_case_usd - 0.15) < 1e-9
    assert summary.time_total_seconds is None


def test_summary_with_time_only() -> None:
    result = _result(
        _iteration(cost=0.0, duration_ms=1000, case_count=2),
        _iteration(cost=0.0, duration_ms=3000, case_count=2),
    )
    summary = compute_cost_time_summary(result)
    assert summary.has_cost is False
    assert summary.has_time is True
    assert summary.time_total_seconds == 4.0
    assert summary.time_per_iteration_seconds == 2.0
    assert summary.time_per_case_seconds == 1.0
    assert summary.cost_total_usd is None


def test_summary_idempotent_under_repeat_calls() -> None:
    # Idempotency check: helpers must be pure.
    result = _result(_iteration(cost=0.10, duration_ms=500, case_count=1))
    a = compute_cost_time_summary(result)
    b = compute_cost_time_summary(result)
    assert a == b
