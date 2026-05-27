"""Decision matrix evaluator.

Pure function in spirit: given the iteration aggregate + baseline +
the experiment's target/decision policy, produce a `DecisionOutcome`
plus a human-readable rationale. No I/O — the OptimizationLoop owns
persistence.

The evaluator implements `DecisionEvaluatorProtocol` from
`selfeval.optimization.loop` so it can be passed in at construction
time. The `decision/` package depends on `optimization` only at type-
check time to keep the import graph acyclic at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from selfeval.optimization.loop import DecisionEvaluatorProtocol
from selfeval.schemas.enums import DecisionOutcome
from selfeval.schemas.experiment import MetricTarget

if TYPE_CHECKING:
    from selfeval.optimization.aggregator import IterationAggregate
    from selfeval.schemas.experiment import Experiment


@dataclass(frozen=True)
class DecisionEvaluation:
    outcome: DecisionOutcome
    rationale: str
    violated_guardrails: list[str] = field(default_factory=list)
    primary_delta: float | None = None


_OUTCOME_FOR_REGRESSION = {
    "reject": DecisionOutcome.REJECT,
    "investigate": DecisionOutcome.INVESTIGATE,
    "spawn_subexperiment": DecisionOutcome.SPAWN_SUBEXPERIMENT,
}

_OUTCOME_FOR_GUARDRAIL = {
    "reject": DecisionOutcome.REJECT,
    "require_tradeoff_review": DecisionOutcome.REQUIRE_TRADEOFF_REVIEW,
}


def _check_operator(value: float, op: str, threshold: float) -> bool:
    """True iff `value op threshold` per the operator string."""
    match op:
        case ">":
            return value > threshold
        case ">=":
            return value >= threshold
        case "<":
            return value < threshold
        case "<=":
            return value <= threshold
        case "==":
            return value == threshold
        case _:  # pragma: no cover — schema validator rejects others
            raise ValueError(f"unknown operator {op!r}")


def _guardrails_violated(
    aggregate: IterationAggregate, guardrails: list[MetricTarget]
) -> list[str]:
    """Return the names of guardrails whose value is out of bounds.

    Guardrails are looked up against `aggregate.guardrails` and (as a
    fallback) `aggregate.reliability`. A guardrail with no observed
    value is treated as passing — we don't fail-shut on missing data
    in MVP because the runner doesn't synthesize every metric.
    """
    violations: list[str] = []
    for g in guardrails:
        value = aggregate.guardrails.get(g.name)
        if value is None:
            value = aggregate.reliability.get(g.name)
        if value is None:
            continue
        if not _check_operator(value, g.operator, g.value):
            violations.append(f"{g.name}={value:.6g} fails {g.operator}{g.value:.6g}")
    return violations


def evaluate_iteration(
    *,
    experiment: Experiment,
    aggregate: IterationAggregate,
    baseline: IterationAggregate | None,
) -> DecisionEvaluation:
    """Apply the §10 canonical subset and return an outcome + rationale."""
    target = experiment.target.primary
    primary_name = target.name
    primary_value = aggregate.primary_value

    primary_delta: float | None = None
    if baseline is not None:
        primary_delta = primary_value - baseline.primary_value

    violations = _guardrails_violated(aggregate, experiment.target.guardrails)
    if violations:
        guardrail_policy = experiment.decision.if_guardrail_fails
        guardrail_outcome = _OUTCOME_FOR_GUARDRAIL.get(
            guardrail_policy, DecisionOutcome.REQUIRE_TRADEOFF_REVIEW
        )
        return DecisionEvaluation(
            outcome=guardrail_outcome,
            rationale=(
                "guardrail(s) violated: " + "; ".join(violations) + f"; policy={guardrail_policy}"
            ),
            violated_guardrails=violations,
            primary_delta=primary_delta,
        )

    # No baseline: the first iteration. Check the absolute target.
    if baseline is None:
        if _check_operator(primary_value, target.operator, target.value):
            return DecisionEvaluation(
                outcome=DecisionOutcome.KEEP_CANDIDATE,
                rationale=(
                    f"first iteration meets target: {primary_name}={primary_value:.6g} "
                    f"{target.operator} {target.value:.6g}"
                ),
                primary_delta=None,
            )
        return DecisionEvaluation(
            outcome=DecisionOutcome.INVESTIGATE,
            rationale=(
                f"first iteration below target: {primary_name}={primary_value:.6g} "
                f"vs target {target.operator} {target.value:.6g}; investigate before bailing"
            ),
            primary_delta=None,
        )

    # Subsequent iterations: compare against baseline and target.
    assert primary_delta is not None
    if primary_delta <= 0:
        # Did not improve.
        if _check_operator(primary_value, target.operator, target.value):
            return DecisionEvaluation(
                outcome=DecisionOutcome.REJECT,
                rationale=(
                    f"no improvement: Δ{primary_name}={primary_delta:+.6g} vs baseline "
                    f"{baseline.primary_value:.6g} (still meets target)"
                ),
                primary_delta=primary_delta,
            )
        # Regressed below target → consult policy.
        regression_policy = experiment.decision.if_regression_fails
        regression_outcome = _OUTCOME_FOR_REGRESSION.get(regression_policy, DecisionOutcome.REJECT)
        return DecisionEvaluation(
            outcome=regression_outcome,
            rationale=(
                f"regression below target: {primary_name}={primary_value:.6g} "
                f"{target.operator} {target.value:.6g}; "
                f"Δ={primary_delta:+.6g}; policy={regression_policy}"
            ),
            primary_delta=primary_delta,
        )

    # Improvement.
    return DecisionEvaluation(
        outcome=DecisionOutcome.KEEP_CANDIDATE,
        rationale=(
            f"improvement: {primary_name}={primary_value:.6g} "
            f"(Δ{primary_delta:+.6g}); guardrails ok"
        ),
        primary_delta=primary_delta,
    )


class DecisionMatrixEvaluator(DecisionEvaluatorProtocol):
    """Object form usable as `DecisionEvaluatorProtocol`."""

    def evaluate(
        self,
        *,
        experiment: Experiment,
        aggregate: IterationAggregate,
        baseline: IterationAggregate | None,
    ) -> tuple[DecisionOutcome, str]:
        ev = evaluate_iteration(experiment=experiment, aggregate=aggregate, baseline=baseline)
        return ev.outcome, ev.rationale
