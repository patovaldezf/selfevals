"""Persist and rehydrate per-case CaseOutcomes for sharded aggregation.

In the sharded model the coordinator never holds the iteration's CaseRuns in
memory — the workers produced them in other processes. So each worker writes one
``scenario_outcomes`` row (the relational form of a ``CaseOutcome``) and the
coordinator rebuilds the list from storage and feeds it to the unchanged
``aggregate_iteration``. This module owns the CaseOutcome<->row mapping and the
storage-backed aggregation entry point.

``scenario_outcomes`` is authoritative for metrics; traces are diagnostic.
Rehydration must be lossless or the sharded run's accuracy would drift from the
in-process golden — the equivalence test in tests/optimization pins that.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from selfevals.graders.base import BreakdownNode, GradeLabel
from selfevals.optimization.aggregator import CaseOutcome, aggregate_iteration

if TYPE_CHECKING:
    from selfevals.optimization.aggregator import IterationAggregate
    from selfevals.storage.interface import StorageInterface


def case_outcome_to_fields(outcome: CaseOutcome) -> dict[str, Any]:
    """Flatten a CaseOutcome into the JSONB-friendly column values.

    Enums become their string values; nested BreakdownNodes use their own
    ``to_dict``. The inverse is ``case_outcome_from_fields``.
    """
    return {
        "case_id": outcome.case_id,
        "labels": [str(label) for label in outcome.per_repetition_label],
        "scores": list(outcome.per_repetition_score),
        "per_grader_labels": {
            grader: [str(label) for label in labels]
            for grader, labels in outcome.per_grader_label.items()
        },
        "failure_modes": list(outcome.failure_modes),
        "breakdowns": [node.to_dict() for node in outcome.breakdowns],
        "failure_weights": dict(outcome.failure_weights),
        "critical_failure_modes": list(outcome.critical_failure_modes),
        "cost_usd": outcome.cost_usd,
        "duration_ms": outcome.duration_ms,
        "llm_call_count": outcome.llm_call_count,
        "cache_hit_count": outcome.cache_hit_count,
    }


def case_outcome_from_fields(fields: dict[str, Any]) -> CaseOutcome:
    """Rebuild a CaseOutcome from its persisted JSONB fields (inverse of above)."""
    return CaseOutcome(
        case_id=fields["case_id"],
        per_repetition_label=[GradeLabel(s) for s in fields["labels"]],
        per_repetition_score=[float(x) for x in fields["scores"]],
        per_grader_label={
            grader: [GradeLabel(s) for s in labels]
            for grader, labels in fields.get("per_grader_labels", {}).items()
        },
        failure_modes=list(fields.get("failure_modes", [])),
        breakdowns=[BreakdownNode.from_dict(d) for d in fields.get("breakdowns", [])],
        failure_weights={k: int(v) for k, v in fields.get("failure_weights", {}).items()},
        critical_failure_modes=list(fields.get("critical_failure_modes", [])),
        cost_usd=float(fields.get("cost_usd", 0.0)),
        duration_ms=int(fields.get("duration_ms", 0)),
        llm_call_count=int(fields.get("llm_call_count", 0)),
        cache_hit_count=int(fields.get("cache_hit_count", 0)),
    )


def aggregate_iteration_from_storage(
    storage: StorageInterface,
    *,
    run_job_id: str,
    iteration: int,
    primary_metric: str,
    reliability_metrics: list[str] | None,
    primary_grader: str | None,
) -> IterationAggregate:
    """Aggregate one iteration's metrics from persisted scenario_outcomes.

    Reads the rows the workers wrote, rehydrates each into a CaseOutcome, and
    feeds the list to the unchanged ``aggregate_iteration`` — the rollups
    (``_rollup_funnel``/``_rollup_confusion``/``_compute_metric``) operate purely
    on ``list[CaseOutcome]``, so no rollup logic is duplicated here. This is the
    coordinator's replacement for the in-memory aggregation path.
    """
    rows = storage.scenario_outcomes_for_iteration(run_job_id=run_job_id, iteration=iteration)
    outcomes = [case_outcome_from_fields(row) for row in rows]
    return aggregate_iteration(
        case_outcomes=outcomes,
        primary_metric=primary_metric,
        reliability_metrics=reliability_metrics,
        primary_grader=primary_grader,
    )
