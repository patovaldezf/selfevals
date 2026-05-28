"""Optimization loop: wire Proposer + Executor + Graders into iterations.

This package contains:

- `Proposer` ABC + concrete `ManualProposer`, `GridProposer`,
  `RandomProposer`. Each produces a stream of `Proposal` objects derived
  from an Experiment's `search_space`, respecting the editable contract.
- `Aggregator`: rolls per-case GradeResults into IterationMetrics
  (primary + guardrails + reliability stats).
- `OptimizationLoop`: the driver. Runs the Experiment state machine
  (draft → queued → running → completed/aborted) one iteration at a
  time, persisting `IterationRecord` rows along the way.
"""

from selfevals.optimization.aggregator import (
    Aggregator,
    CaseOutcome,
    FunnelNode,
    IterationAggregate,
    aggregate_iteration,
)
from selfevals.optimization.loop import (
    IterationOutcome,
    OptimizationLoop,
    OptimizationResult,
)
from selfevals.optimization.proposers import (
    GridProposer,
    ManualProposer,
    Proposer,
    RandomProposer,
    SearchSpaceExhaustedError,
)
from selfevals.optimization.sampling import (
    OptimizationSplit,
    select_optimization_set,
)

__all__ = [
    "Aggregator",
    "CaseOutcome",
    "FunnelNode",
    "GridProposer",
    "IterationAggregate",
    "IterationOutcome",
    "ManualProposer",
    "OptimizationLoop",
    "OptimizationResult",
    "OptimizationSplit",
    "Proposer",
    "RandomProposer",
    "SearchSpaceExhaustedError",
    "aggregate_iteration",
    "select_optimization_set",
]
