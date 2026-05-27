"""Decision matrix: §10 canonical subset -> DecisionOutcome.

`DecisionMatrixEvaluator` plugs into `OptimizationLoop` as a
`DecisionEvaluatorProtocol`. It receives the current iteration's
`IterationAggregate` plus the baseline aggregate (the best previous
iteration, or None for the first one) and returns a
`(DecisionOutcome, rationale: str)` tuple.

The decision tree, in order:

1. If any **guardrail** declared on `Experiment.target.guardrails` is
   violated → `REQUIRE_TRADEOFF_REVIEW` (or `REJECT` if policy says so).
2. If the iteration **regressed** on a gate dataset metric → outcome
   per `experiment.decision.if_regression_fails`.
3. If primary metric **did not improve** vs baseline → `REJECT`.
4. If primary metric **dropped** below the absolute target → `INVESTIGATE`.
5. Otherwise → `KEEP_CANDIDATE`.

This is a deliberately small slice of the canonical matrix — enough to
power MVP optimization runs without baking in policy that should belong
to the user. Each branch records why it fired in the rationale string.
"""

from selfevals.decision.matrix import (
    DecisionEvaluation,
    DecisionMatrixEvaluator,
    evaluate_iteration,
)

__all__ = [
    "DecisionEvaluation",
    "DecisionMatrixEvaluator",
    "evaluate_iteration",
]
