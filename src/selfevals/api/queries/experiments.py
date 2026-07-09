"""Experiment listing, detail, decisions, iteration drill-down, funnel, and compare."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from selfevals.api.queries._shared import experiment_summary_dict, iteration_summaries
from selfevals.api.schemas import (
    CaseListResponse,
    CompareFailureModes,
    CompareFunnelRow,
    CompareMetricRow,
    CompareParamRow,
    CompareRecommendation,
    CompareResponse,
    ExperimentDetailResponse,
    ExperimentListPage,
    ExperimentSummary,
    FunnelNodeResponse,
    FunnelResponse,
    IterationSummary,
)
from selfevals.cli._common import (
    _experiment_decisions,
    _experiment_iterations,
    _reconstruct_result,
)
from selfevals.reporter import render_json
from selfevals.reporter.compare import compute_compare
from selfevals.schemas.enums import ExperimentState
from selfevals.schemas.experiment import Experiment
from selfevals.schemas.iteration import IterationRecord
from selfevals.storage.errors import EntityNotFoundError
from selfevals.storage.interface import ListFilter, StorageInterface

from ._shared import case_summary


class AnchorPoint(BaseModel):
    experiment_id: str
    experiment_name: str
    iteration: int
    primary_metric_name: str
    primary_metric_value: float
    decision_outcome: str
    created_at: str


def list_experiments(
    storage: StorageInterface,
    *,
    workspace_id: str,
    limit: int = 100,
    offset: int = 0,
    state: ExperimentState | None = None,
    feature: str | None = None,
) -> ExperimentListPage:
    """Paginated experiments listing (A8), with optional filters.

    `ListFilter` already supports limit/offset; we expose it here and
    return a `total` so the FE can show "X of N" without a second
    round-trip. The iteration-count subquery is intentionally a
    full-scan per experiment — at the volumes we're targeting
    (Fase A: <100 experiments), this is correct-and-cheap.

    Filters are applied in memory *before* pagination so `total`/`has_more`
    describe the filtered set, not the whole workspace. `state` could ride
    `ListFilter.where` (it is a scalar `json_extract`), but `feature` is
    membership in the nested `taxonomy.target_features` list, which the
    scalar `where` cannot express — so both filter here, keeping the logic
    in one place. If volumes grow, `state` is the field to promote to a real
    column (same note as m0001), and `target_features` to a join/`json_each`.
    """
    experiments, total, iteration_counts = storage.list_experiments_page(
        workspace_id=workspace_id,
        limit=limit,
        offset=offset,
        state=str(state) if state is not None else None,
        feature=feature,
    )
    return ExperimentListPage(
        items=[
            ExperimentSummary(
                **experiment_summary_dict(exp, iteration_count=iteration_counts.get(exp.id, 0))
            )
            for exp in experiments
        ],
        total=total,
        limit=limit,
        offset=offset,
        has_more=(offset + limit) < total,
    )


def experiment_detail(
    storage: StorageInterface, *, workspace_id: str, experiment_id: str
) -> ExperimentDetailResponse | None:
    result_dict: dict[str, Any] | None = None
    with storage.open(workspace_id) as scope:
        try:
            exp = scope.get_entity(Experiment, experiment_id)
        except EntityNotFoundError:
            return None
        assert isinstance(exp, Experiment)
        iterations = _experiment_iterations(scope, exp.id)
        decisions = _experiment_decisions(scope, exp.id)
        # Reconstruct while the scope is open — it reloads persisted Traces to
        # repopulate case_runs / failure_reasons.
        if iterations:
            result = _reconstruct_result(scope, exp, iterations, decisions)
            result_dict = json.loads(render_json(result))

    summary = ExperimentSummary(**experiment_summary_dict(exp, iteration_count=len(iterations)))
    best_iteration = result_dict.get("best_iteration") if result_dict else None
    return ExperimentDetailResponse(
        summary=summary,
        result=result_dict,
        iterations=iteration_summaries(iterations, decisions),
        best_iteration=best_iteration,
    )


def experiment_iterations(
    storage: StorageInterface, *, workspace_id: str, experiment_id: str
) -> list[IterationSummary]:
    with storage.open(workspace_id) as scope:
        iterations = _experiment_iterations(scope, experiment_id)
        decisions = _experiment_decisions(scope, experiment_id)
    return iteration_summaries(iterations, decisions)


def experiment_cases(
    storage: StorageInterface, *, workspace_id: str, experiment_id: str
) -> CaseListResponse:
    """List the eval cases persisted under an experiment.

    Cases are written at launch time (`runner.launch._persist_cases`) stamped
    with `experiment_id`, so the storage `where` filter (json_extract) scopes
    them without a dedicated column. Holdout cases are included and flagged —
    the set is reported honestly, not silently trimmed to the optimization
    cases. Ordered by name for a stable, scannable list.
    """
    cases = storage.eval_cases_for_experiment(workspace_id, experiment_id)
    trace_refs = storage.latest_trace_refs_by_case(workspace_id, experiment_id)
    cases.sort(key=lambda c: c.name)
    summaries = [case_summary(c, trace_refs.get(c.id)) for c in cases]
    holdout_count = sum(1 for c in cases if c.holdout)
    return CaseListResponse(
        cases=summaries,
        total=len(summaries),
        holdout_count=holdout_count,
    )


def experiment_decisions(
    storage: StorageInterface, *, workspace_id: str, experiment_id: str
) -> list[dict[str, Any]]:
    with storage.open(workspace_id) as scope:
        decisions = _experiment_decisions(scope, experiment_id)
    out: list[dict[str, Any]] = []
    for iteration in sorted(decisions):
        d = decisions[iteration]
        out.append(
            {
                "id": d.id,
                "iteration": d.iteration,
                "outcome": str(d.outcome),
                "automated_rationale": d.rationale.automated,
                "human_rationale": (d.rationale.human.notes if d.rationale.human else None),
                "metrics_snapshot": d.metrics_snapshot,
                "created_at": d.created_at.isoformat(),
            }
        )
    return out


def iteration_detail(
    storage: StorageInterface, *, workspace_id: str, iteration_id: str
) -> dict[str, Any] | None:
    with storage.open(workspace_id) as scope:
        try:
            it = scope.get_entity(IterationRecord, iteration_id)
        except EntityNotFoundError:
            return None
        assert isinstance(it, IterationRecord)
        decisions = _experiment_decisions(scope, it.experiment_id)
    decision = decisions.get(it.iteration)
    return {
        "iteration": it.model_dump(mode="json"),
        "decision": decision.model_dump(mode="json") if decision else None,
    }


def load_iteration_funnel(
    storage: StorageInterface, *, workspace_id: str, iteration_id: str
) -> FunnelResponse | None:
    """The grader funnel drill-down for a single iteration (B2).

    We read the funnel DIRECTLY from `IterationRecord.metrics.funnel` — the
    persisted source of truth — and deliberately bypass `_reconstruct_result`:
    that helper rebuilds the `OptimizationResult` JSON for the reporter but
    does NOT rehydrate `aggregate.funnel`, so the funnel inside
    `ExperimentDetailResponse.result` is always empty. Touching it is out of
    scope (it would ripple into the reporter tests); this dedicated endpoint
    sidesteps the gap entirely.

    Returns None for an unknown iteration (→ 404 at the route). An iteration
    with no breakdown yields `nodes == {}`, which is correct, not an error:
    most graders (e.g. the pingpong example) emit no structured breakdown.
    """
    with storage.open(workspace_id) as scope:
        try:
            it = scope.get_entity(IterationRecord, iteration_id)
        except EntityNotFoundError:
            return None
        assert isinstance(it, IterationRecord)
    funnel = it.metrics.funnel if it.metrics is not None else {}
    nodes = {k: FunnelNodeResponse.model_validate(v) for k, v in (funnel or {}).items()}
    return FunnelResponse(iteration_id=it.id, iteration=it.iteration, nodes=nodes)


def load_compare(
    storage: StorageInterface,
    *,
    workspace_id: str,
    experiment_id: str,
    a_id: str,
    b_id: str,
) -> CompareResponse | None:
    """Structured diff of two IterationRecords (B3).

    Mirrors the CLI's `cmd_compare` loading: open the workspace scope,
    fetch both records by id. Returns None when either iteration is
    missing (the handler maps that to 404). Raises ValueError when an
    iteration belongs to a different experiment than the one in the path,
    or when the two iterations belong to different experiments (the
    handler maps that to 400). Delegates all comparison math to the
    reporter's `compute_compare` — the single source of truth shared with
    the CLI — then projects the frozen dataclass into pydantic here so the
    reporter stays free of the web layer.

    `holdout_status` is "unavailable": an `IterationRecord` carries no
    split classification, so we do not fabricate a holdout number.
    """
    with storage.open(workspace_id) as scope:
        try:
            a = scope.get_entity(IterationRecord, a_id)
            b = scope.get_entity(IterationRecord, b_id)
        except EntityNotFoundError:
            return None
    assert isinstance(a, IterationRecord)
    assert isinstance(b, IterationRecord)
    if a.experiment_id != experiment_id or b.experiment_id != experiment_id:
        raise ValueError(
            "iterations must belong to the experiment in the path "
            f"({experiment_id}); got A={a.experiment_id} B={b.experiment_id}"
        )

    result = compute_compare(a, b)
    rec = result.recommendation
    return CompareResponse(
        a_id=result.a_id,
        b_id=result.b_id,
        a_iteration=result.a_iteration,
        b_iteration=result.b_iteration,
        a_created_at=result.a_created_at,
        b_created_at=result.b_created_at,
        a_decision=result.a_decision,
        b_decision=result.b_decision,
        proposal_diff=[
            CompareParamRow(key=r.key, a=r.a, b=r.b, changed=r.changed)
            for r in result.proposal_diff
        ],
        metrics_diff=[
            CompareMetricRow(name=r.name, a=r.a, b=r.b, delta=r.delta) for r in result.metrics_diff
        ],
        failure_modes=CompareFailureModes(
            only_a=dict(result.failure_modes.only_a),
            only_b=dict(result.failure_modes.only_b),
            common=dict(result.failure_modes.common),
        ),
        funnel_diff=[
            CompareFunnelRow(path=r.path, a=r.a, b=r.b, delta=r.delta) for r in result.funnel_diff
        ],
        recommendation=CompareRecommendation(
            kind=rec.kind,
            winner=rec.winner,
            metric_name=rec.metric_name,
            a_metric_name=rec.a_metric_name,
            b_metric_name=rec.b_metric_name,
            a_value=rec.a_value,
            b_value=rec.b_value,
            delta=rec.delta,
            new_failure_modes=list(rec.new_failure_modes),
        ),
        holdout_status="unavailable",
    )


def anchor_set_history(storage: StorageInterface, *, workspace_id: str) -> list[AnchorPoint]:
    """Longitudinal view: latest primary-metric value per experiment.

    Anchor-set proper requires repeated reruns of a canonical case
    set; until that lands, we expose the per-experiment latest
    completed iteration so the chart has shape.
    """
    with storage.open(workspace_id) as scope:
        experiments = [
            e for e in scope.list_entities(Experiment, ListFilter()) if isinstance(e, Experiment)
        ]
        points: list[AnchorPoint] = []
        for exp in experiments:
            iterations = _experiment_iterations(scope, exp.id)
            decisions = _experiment_decisions(scope, exp.id)
            for it in iterations:
                if it.metrics is None:
                    continue
                decision = decisions.get(it.iteration)
                outcome = str(decision.outcome) if decision else "unknown"
                points.append(
                    AnchorPoint(
                        experiment_id=exp.id,
                        experiment_name=exp.name,
                        iteration=it.iteration,
                        primary_metric_name=it.metrics.primary.name,
                        primary_metric_value=it.metrics.primary.value,
                        decision_outcome=outcome,
                        created_at=it.created_at.isoformat(),
                    )
                )
    points.sort(key=lambda p: p.created_at)
    return points
