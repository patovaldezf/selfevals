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

from selfeval.optimization.aggregator import (
    Aggregator,
    CaseOutcome,
    IterationAggregate,
    aggregate_iteration,
)
from selfeval.optimization.loop import (
    IterationOutcome,
    OptimizationLoop,
    OptimizationResult,
)
from selfeval.optimization.proposers import (
    GridProposer,
    ManualProposer,
    Proposer,
    RandomProposer,
    SearchSpaceExhaustedError,
)

__all__ = [
    "Aggregator",
    "CaseOutcome",
    "GridProposer",
    "IterationAggregate",
    "IterationOutcome",
    "ManualProposer",
    "OptimizationLoop",
    "OptimizationResult",
    "Proposer",
    "RandomProposer",
    "SearchSpaceExhaustedError",
    "aggregate_iteration",
]
