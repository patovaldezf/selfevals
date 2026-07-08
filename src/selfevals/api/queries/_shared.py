"""Projection helpers shared across the queries.* modules."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from selfevals.api.schemas import CaseSummary, FeatureRef, IterationSummary, SpanSummary
from selfevals.schemas.eval_case import EvalCase
from selfevals.schemas.experiment import Experiment
from selfevals.schemas.iteration import DecisionRecord, IterationRecord
from selfevals.trace.span_view import span_view


def experiment_summary_dict(exp: Experiment, *, iteration_count: int) -> dict[str, Any]:
    return {
        "id": exp.id,
        "name": exp.name,
        "goal": exp.goal,
        "mode": str(exp.mode),
        "state": str(exp.state),
        "primary_metric": exp.target.primary.name,
        "primary_target": {
            "operator": exp.target.primary.operator,
            "value": exp.target.primary.value,
        },
        "proposer_strategy": str(exp.proposer.strategy),
        "max_iterations": exp.run.max_iterations,
        "created_at": exp.created_at.isoformat(),
        "updated_at": exp.updated_at.isoformat(),
        "iteration_count": iteration_count,
    }


def iteration_summaries(
    iterations: Sequence[IterationRecord],
    decisions: dict[int, DecisionRecord],
) -> list[IterationSummary]:
    best_so_far: float | None = None
    out: list[IterationSummary] = []
    for it in iterations:
        primary = it.metrics.primary if it.metrics else None
        delta: float | None = None
        if primary is not None:
            delta = 0.0 if best_so_far is None else primary.value - best_so_far
            if best_so_far is None or primary.value > best_so_far:
                best_so_far = primary.value
        decision = decisions.get(it.iteration)
        out.append(
            IterationSummary(
                id=it.id,
                iteration=it.iteration,
                state=str(it.state),
                hypothesis=it.hypothesis,
                proposed_parameters=dict(it.proposed_parameters),
                primary_metric_name=primary.name if primary else None,
                primary_metric_value=primary.value if primary else None,
                delta_vs_best=delta,
                decision_outcome=(str(decision.outcome) if decision is not None else None),
                decision_rationale=(decision.rationale.automated if decision is not None else None),
                cost_usd=it.cost_usd,
                duration_seconds=it.duration_seconds,
                trace_run_ids=list(it.execution.trace_run_ids),
                created_at=it.created_at,
            )
        )
    return out


def span_summary(span: Any) -> SpanSummary:
    """Project any Span subclass into the `SpanSummary` view model.

    Delegates the field selection to `trace.span_view.span_view`, the
    shared projection the live SSE path also uses, so a persisted span and
    its live twin render identically. This function only adapts that dict
    into the Pydantic model for the REST snapshot.
    """
    return SpanSummary(**span_view(span))


def case_summary(case: EvalCase, trace_ref: tuple[str, str] | None = None) -> CaseSummary:
    latest_run_id = trace_ref[0] if trace_ref is not None else None
    latest_trace_id = trace_ref[1] if trace_ref is not None else None
    return CaseSummary(
        id=case.id,
        name=case.name,
        task_type=case.task_type,
        modalities=[str(m) for m in case.modalities],
        input=case.input,
        graders=list(case.graders),
        holdout=case.holdout,
        is_conversation=case.is_conversation(),
        latest_run_id=latest_run_id,
        latest_trace_id=latest_trace_id,
        feature=FeatureRef(
            primary=str(case.taxonomy.feature.primary),
            secondary=[str(s) for s in case.taxonomy.feature.secondary],
        )
        if case.taxonomy.feature is not None
        else None,
        level=str(case.taxonomy.level) if case.taxonomy.level is not None else None,
        dataset_type=(
            str(case.taxonomy.dataset_type) if case.taxonomy.dataset_type is not None else None
        ),
    )
