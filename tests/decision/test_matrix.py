from __future__ import annotations

import pytest

from selfevals.decision.matrix import DecisionMatrixEvaluator, evaluate_iteration
from selfevals.optimization.aggregator import IterationAggregate
from selfevals.schemas._base import EntityRef
from selfevals.schemas.enums import (
    DatasetType,
    DecisionOutcome,
    Mode,
    ProposerStrategy,
    SandboxMode,
)
from selfevals.schemas.experiment import (
    DatasetUsage,
    DecisionPolicy,
    Experiment,
    ExperimentTaxonomy,
    FrozenSnapshot,
    MetricTarget,
    ProposerSpec,
    RunSpec,
    TargetSpec,
)

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _experiment(
    *,
    target_op: str = ">=",
    target_value: float = 0.85,
    guardrails: list[MetricTarget] | None = None,
    if_regression_fails: str = "reject",
    if_guardrail_fails: str = "require_tradeoff_review",
) -> Experiment:
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
        target=TargetSpec(
            primary=MetricTarget(name="pass@1", operator=target_op, value=target_value),
            guardrails=guardrails or [],
        ),
        frozen=FrozenSnapshot(
            fleet=EntityRef(id="flt_x"),
            agents=[EntityRef(id="ag_x")],
            datasets=[EntityRef(id="ds_y")],
        ),
        proposer=ProposerSpec(strategy=ProposerStrategy.GRID),
        run=RunSpec(sandbox=SandboxMode.DRY_RUN),
        decision=DecisionPolicy(
            if_regression_fails=if_regression_fails,  # type: ignore[arg-type]
            if_guardrail_fails=if_guardrail_fails,  # type: ignore[arg-type]
        ),
    )


def _agg(*, primary: float, guardrails: dict[str, float] | None = None) -> IterationAggregate:
    return IterationAggregate(
        primary_metric="pass@1",
        primary_value=primary,
        guardrails=guardrails or {},
    )


def test_first_iteration_meeting_target_kept() -> None:
    exp = _experiment(target_op=">=", target_value=0.85)
    ev = evaluate_iteration(experiment=exp, aggregate=_agg(primary=0.9), baseline=None)
    assert ev.outcome == DecisionOutcome.KEEP_CANDIDATE
    assert "meets target" in ev.rationale


def test_first_iteration_below_target_investigated() -> None:
    exp = _experiment(target_op=">=", target_value=0.85)
    ev = evaluate_iteration(experiment=exp, aggregate=_agg(primary=0.6), baseline=None)
    assert ev.outcome == DecisionOutcome.INVESTIGATE


def test_improvement_above_baseline_kept() -> None:
    exp = _experiment()
    ev = evaluate_iteration(
        experiment=exp,
        aggregate=_agg(primary=0.92),
        baseline=_agg(primary=0.85),
    )
    assert ev.outcome == DecisionOutcome.KEEP_CANDIDATE
    assert ev.primary_delta == pytest.approx(0.07)


def test_no_improvement_but_still_meets_target_rejected() -> None:
    exp = _experiment(target_op=">=", target_value=0.85)
    ev = evaluate_iteration(
        experiment=exp,
        aggregate=_agg(primary=0.85),
        baseline=_agg(primary=0.85),
    )
    assert ev.outcome == DecisionOutcome.REJECT
    assert "no improvement" in ev.rationale


def test_regression_below_target_uses_policy_reject() -> None:
    exp = _experiment(if_regression_fails="reject")
    ev = evaluate_iteration(
        experiment=exp,
        aggregate=_agg(primary=0.4),  # well below target 0.85
        baseline=_agg(primary=0.85),
    )
    assert ev.outcome == DecisionOutcome.REJECT
    assert "regression" in ev.rationale


def test_regression_policy_investigate() -> None:
    exp = _experiment(if_regression_fails="investigate")
    ev = evaluate_iteration(
        experiment=exp,
        aggregate=_agg(primary=0.5),
        baseline=_agg(primary=0.85),
    )
    assert ev.outcome == DecisionOutcome.INVESTIGATE


def test_regression_policy_spawn_subexperiment() -> None:
    exp = _experiment(if_regression_fails="spawn_subexperiment")
    ev = evaluate_iteration(
        experiment=exp,
        aggregate=_agg(primary=0.5),
        baseline=_agg(primary=0.85),
    )
    assert ev.outcome == DecisionOutcome.SPAWN_SUBEXPERIMENT


def test_guardrail_violation_triggers_review() -> None:
    exp = _experiment(
        guardrails=[MetricTarget(name="cost_usd_per_case", operator="<=", value=0.02)],
    )
    ev = evaluate_iteration(
        experiment=exp,
        aggregate=_agg(primary=0.95, guardrails={"cost_usd_per_case": 0.05}),
        baseline=_agg(primary=0.9),
    )
    assert ev.outcome == DecisionOutcome.REQUIRE_TRADEOFF_REVIEW
    assert "guardrail(s) violated" in ev.rationale
    assert ev.violated_guardrails


def test_guardrail_policy_reject() -> None:
    exp = _experiment(
        guardrails=[MetricTarget(name="cost_usd_per_case", operator="<=", value=0.02)],
        if_guardrail_fails="reject",
    )
    ev = evaluate_iteration(
        experiment=exp,
        aggregate=_agg(primary=0.95, guardrails={"cost_usd_per_case": 0.1}),
        baseline=_agg(primary=0.9),
    )
    assert ev.outcome == DecisionOutcome.REJECT


def test_guardrail_missing_metric_treated_as_passing() -> None:
    """The runner doesn't always synthesize every metric in MVP."""
    exp = _experiment(
        guardrails=[MetricTarget(name="latency_ms_p95", operator="<=", value=2000)],
    )
    ev = evaluate_iteration(
        experiment=exp,
        aggregate=_agg(primary=0.9),  # no latency in this aggregate
        baseline=_agg(primary=0.85),
    )
    assert ev.outcome == DecisionOutcome.KEEP_CANDIDATE


def test_object_form_returns_tuple() -> None:
    exp = _experiment()
    out, rationale = DecisionMatrixEvaluator().evaluate(
        experiment=exp,
        aggregate=_agg(primary=0.95),
        baseline=_agg(primary=0.9),
    )
    assert out == DecisionOutcome.KEEP_CANDIDATE
    assert isinstance(rationale, str)


@pytest.mark.parametrize(
    ("op", "value", "agg", "expected"),
    [
        (">", 0.5, 0.6, DecisionOutcome.KEEP_CANDIDATE),
        (">", 0.5, 0.5, DecisionOutcome.INVESTIGATE),
        ("<=", 0.5, 0.4, DecisionOutcome.KEEP_CANDIDATE),
        ("<=", 0.5, 0.6, DecisionOutcome.INVESTIGATE),
        ("==", 0.5, 0.5, DecisionOutcome.KEEP_CANDIDATE),
        ("==", 0.5, 0.51, DecisionOutcome.INVESTIGATE),
    ],
)
def test_operator_branches(op: str, value: float, agg: float, expected: DecisionOutcome) -> None:
    exp = _experiment(target_op=op, target_value=value)
    ev = evaluate_iteration(experiment=exp, aggregate=_agg(primary=agg), baseline=None)
    assert ev.outcome == expected


# --- G1: critical tier (zero-tolerance) gates via the existing guardrail path --


def test_critical_failure_count_gate_fails_when_nonzero() -> None:
    # The readiness gate for autopilot: a guardrail `critical_failure_count == 0`
    # must trip when a critical mode occurred — using the existing guardrail
    # mechanism, no change to matrix.py.
    exp = _experiment(
        guardrails=[MetricTarget(name="critical_failure_count", operator="==", value=0)],
        if_guardrail_fails="reject",
    )
    ev = evaluate_iteration(
        experiment=exp,
        aggregate=_agg(primary=0.95, guardrails={"critical_failure_count": 2.0}),
        baseline=_agg(primary=0.9),
    )
    assert ev.outcome == DecisionOutcome.REJECT
    assert ev.violated_guardrails


def test_critical_failure_count_gate_passes_at_zero() -> None:
    # High plain accuracy AND zero critical failures → the gate is satisfied.
    exp = _experiment(
        guardrails=[MetricTarget(name="critical_failure_count", operator="==", value=0)],
    )
    ev = evaluate_iteration(
        experiment=exp,
        aggregate=_agg(primary=0.95, guardrails={"critical_failure_count": 0.0}),
        baseline=None,
    )
    assert ev.outcome == DecisionOutcome.KEEP_CANDIDATE


def test_weighted_failure_per_case_guardrail() -> None:
    exp = _experiment(
        guardrails=[MetricTarget(name="weighted_failure_per_case", operator="<=", value=0.5)],
        if_guardrail_fails="reject",
    )
    ev = evaluate_iteration(
        experiment=exp,
        aggregate=_agg(primary=0.95, guardrails={"weighted_failure_per_case": 1.2}),
        baseline=_agg(primary=0.9),
    )
    assert ev.outcome == DecisionOutcome.REJECT
