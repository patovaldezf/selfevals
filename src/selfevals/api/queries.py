"""Read queries over the SQLite store, shaped for the web UI.

We don't add an ORM. We open a `WorkspaceScope` per request, list
entities, and project them into the view models in
`selfevals.api.schemas`. The single non-trivial bit is rebuilding
the `OptimizationResult` JSON via the existing reconstruction helper
in `cli.commands` so the web reuses the exact same shape the
reporter emits.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol, cast

from pydantic import BaseModel

from selfevals.api.schemas import (
    CaseListResponse,
    CaseResultRow,
    CaseSummary,
    CompareFailureModes,
    CompareFunnelRow,
    CompareMetricRow,
    CompareParamRow,
    CompareRecommendation,
    CompareResponse,
    ExperimentDetailResponse,
    ExperimentListPage,
    ExperimentResultsResponse,
    ExperimentSummary,
    FeatureRef,
    FunnelNodeResponse,
    FunnelResponse,
    IterationSummary,
    SpanSummary,
    ThreadResponse,
    ThreadTurn,
    TraceResponse,
    WorkspaceResponse,
    WorkspaceSummary,
)
from selfevals.cli.commands import (
    _experiment_decisions,
    _experiment_iterations,
    _reconstruct_result,
)
from selfevals.reporter import render_json
from selfevals.reporter.compare import compute_compare
from selfevals.schemas.enums import ExperimentState
from selfevals.schemas.eval_case import EvalCase
from selfevals.schemas.experiment import Experiment
from selfevals.schemas.iteration import DecisionRecord, IterationRecord
from selfevals.schemas.trace import LLMCallSpan, ToolCallSpan, Trace
from selfevals.schemas.workspace import Workspace
from selfevals.storage.interface import ListFilter, StorageInterface
from selfevals.trace.span_view import span_view


class AnchorPoint(BaseModel):
    experiment_id: str
    experiment_name: str
    iteration: int
    primary_metric_name: str
    primary_metric_value: float
    decision_outcome: str
    created_at: str


class _ConnectionBacked(Protocol):
    @property
    def connection(self) -> Any: ...


def _connection(storage: StorageInterface) -> Any:
    return cast(_ConnectionBacked, storage).connection


def list_workspaces(storage: StorageInterface) -> list[WorkspaceSummary]:
    """Cross-workspace listing. Direct SQL because the typed interface is
    intentionally scoped — no way to list without a workspace_id."""
    hot = getattr(storage, "list_workspace_summaries", None)
    if callable(hot):
        return cast(list[WorkspaceSummary], hot())
    conn = _connection(storage)
    rows = conn.execute(
        "SELECT payload FROM entities WHERE entity_type = 'Workspace' ORDER BY created_at DESC"
    ).fetchall()
    summaries: list[WorkspaceSummary] = []
    for (payload,) in rows:
        ws = Workspace.model_validate(json.loads(payload))
        exp_count = conn.execute(
            "SELECT COUNT(1) FROM entities WHERE entity_type = 'Experiment' AND workspace_id = ?",
            (ws.id,),
        ).fetchone()[0]
        last_run = conn.execute(
            "SELECT MAX(updated_at) FROM entities "
            "WHERE entity_type = 'IterationRecord' AND workspace_id = ?",
            (ws.id,),
        ).fetchone()[0]
        summaries.append(
            WorkspaceSummary(
                id=ws.id,
                slug=ws.slug,
                name=ws.name,
                description=ws.description,
                owner_id=ws.owner_id,
                created_at=ws.created_at,
                experiment_count=int(exp_count or 0),
                last_run_at=last_run,
            )
        )
    return summaries


def workspace_detail(storage: StorageInterface, *, workspace_id: str) -> WorkspaceResponse | None:
    try:
        with storage.open(workspace_id) as scope:
            ws = scope.get_entity(Workspace, workspace_id)
            assert isinstance(ws, Workspace)
            experiments: Sequence[Experiment] = [
                e
                for e in scope.list_entities(Experiment, ListFilter())
                if isinstance(e, Experiment)
            ]
            recent_iterations = [
                it
                for it in scope.list_entities(
                    IterationRecord,
                    ListFilter(order_by="updated_at", limit=20),
                )
                if isinstance(it, IterationRecord)
            ]
    except Exception:
        return None
    keep_count = sum(
        1
        for it in recent_iterations
        if it.decision is not None and str(it.decision.outcome) == "keep_candidate"
    )
    recent_health: float | None = None
    if recent_iterations:
        recent_health = round(keep_count / len(recent_iterations), 3)
    return WorkspaceResponse(
        id=ws.id,
        slug=ws.slug,
        name=ws.name,
        description=ws.description,
        owner_id=ws.owner_id,
        created_at=ws.created_at,
        experiment_count=len(experiments),
        recent_health=recent_health,
    )


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
    hot = getattr(storage, "list_experiments_page", None)
    if callable(hot):
        experiments, total, hot_iteration_counts = hot(
            workspace_id=workspace_id,
            limit=limit,
            offset=offset,
            state=str(state) if state is not None else None,
            feature=feature,
        )
        return ExperimentListPage(
            items=[
                ExperimentSummary(
                    **_experiment_summary_dict(
                        exp, iteration_count=hot_iteration_counts.get(exp.id, 0)
                    )
                )
                for exp in experiments
            ],
            total=total,
            limit=limit,
            offset=offset,
            has_more=(offset + limit) < total,
        )
    with storage.open(workspace_id) as scope:
        all_experiments = [
            e
            for e in scope.list_entities(Experiment, ListFilter(order_by="updated_at"))
            if isinstance(e, Experiment)
        ]
        if state is not None:
            all_experiments = [e for e in all_experiments if e.state == state]
        if feature is not None:
            all_experiments = [
                e for e in all_experiments if feature in e.taxonomy.target_features
            ]
        total = len(all_experiments)
        page = all_experiments[offset : offset + limit]
        all_iterations = [
            it
            for it in scope.list_entities(IterationRecord, ListFilter())
            if isinstance(it, IterationRecord)
        ]
        iteration_counts: dict[str, int] = {}
        for it in all_iterations:
            iteration_counts[it.experiment_id] = iteration_counts.get(it.experiment_id, 0) + 1
        items = [
            ExperimentSummary(
                **_experiment_summary_dict(exp, iteration_count=iteration_counts.get(exp.id, 0))
            )
            for exp in page
        ]
    return ExperimentListPage(
        items=items,
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
        except Exception:
            return None
        assert isinstance(exp, Experiment)
        iterations = _experiment_iterations(scope, exp.id)
        decisions = _experiment_decisions(scope, exp.id)
        # Reconstruct while the scope is open — it reloads persisted Traces to
        # repopulate case_runs / failure_reasons.
        if iterations:
            result = _reconstruct_result(scope, exp, iterations, decisions)
            result_dict = json.loads(render_json(result))

    summary = ExperimentSummary(**_experiment_summary_dict(exp, iteration_count=len(iterations)))
    best_iteration = result_dict.get("best_iteration") if result_dict else None
    return ExperimentDetailResponse(
        summary=summary,
        result=result_dict,
        iterations=_iteration_summaries(iterations, decisions),
        best_iteration=best_iteration,
    )


def experiment_iterations(
    storage: StorageInterface, *, workspace_id: str, experiment_id: str
) -> list[IterationSummary]:
    with storage.open(workspace_id) as scope:
        iterations = _experiment_iterations(scope, experiment_id)
        decisions = _experiment_decisions(scope, experiment_id)
    return _iteration_summaries(iterations, decisions)


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
    hot_cases = getattr(storage, "eval_cases_for_experiment", None)
    hot_refs = getattr(storage, "latest_trace_refs_by_case", None)
    if callable(hot_cases) and callable(hot_refs):
        cases = hot_cases(workspace_id, experiment_id)
        trace_refs = hot_refs(workspace_id, experiment_id)
    else:
        with storage.open(workspace_id) as scope:
            cases = [
                c
                for c in scope.list_entities(
                    EvalCase, ListFilter(where={"experiment_id": experiment_id})
                )
                if isinstance(c, EvalCase)
            ]
            trace_refs = _latest_trace_per_case(scope, experiment_id)
    cases.sort(key=lambda c: c.name)
    summaries = [_case_summary(c, trace_refs.get(c.id)) for c in cases]
    holdout_count = sum(1 for c in cases if c.holdout)
    return CaseListResponse(
        cases=summaries,
        total=len(summaries),
        holdout_count=holdout_count,
    )


def _latest_trace_per_case(scope: Any, experiment_id: str) -> dict[str, tuple[str, str]]:
    """Map each eval_case_id → (run_id, trace_id) of its most recent persisted
    trace in the experiment, so the cases list can link case → trace.

    "Most recent" = highest `run.iteration`, then latest `environment.started_at`
    as a tie-break across repetitions. Cases whose traces were never persisted
    (e.g. they passed under `persist_traces="failed"`) simply don't appear in the
    map → the summary's trace ids stay None (honest). One scan, same Trace filter
    as `_load_case_runs`."""
    traces = [
        t
        for t in scope.list_entities(
            Trace, ListFilter(where={"run.experiment_id": experiment_id})
        )
        if isinstance(t, Trace)
    ]
    best: dict[str, tuple[int, datetime, str, str]] = {}
    for t in traces:
        case_id = t.run.eval_case_id
        if case_id is None:
            continue
        iteration = t.run.iteration if t.run.iteration is not None else -1
        key = (iteration, t.environment.started_at)
        current = best.get(case_id)
        if current is None or key > (current[0], current[1]):
            best[case_id] = (iteration, t.environment.started_at, t.run.run_id, t.id)
    return {case_id: (run_id, trace_id) for case_id, (_i, _s, run_id, trace_id) in best.items()}


def _case_summary(case: EvalCase, trace_ref: tuple[str, str] | None = None) -> CaseSummary:
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


def experiment_results(
    storage: StorageInterface, *, workspace_id: str, experiment_id: str
) -> ExperimentResultsResponse | None:
    """Per-scenario results for the experiment's best iteration (the FE's
    expected/detected/matched grid).

    `best_iteration.failure_reasons` says *that* something failed but drops the
    `case_id`, the expectation, and what was produced. This rebuilds the picture
    per case: the best iteration is the one with the highest primary metric (same
    rule as `OptimizationLoop.best_iteration`); we read its persisted traces
    (grouped by `eval_case_id`, like `_load_case_runs`) for `detected` + the
    graders' verdicts, and cross-reference the persisted `EvalCase` for
    `expected`. Cases with no persisted trace (passing cases under
    `persist_traces="failed"`) are still listed — `detected`/`matched` None — so
    the set is honest, not silently trimmed to the failures.

    Returns None for an unknown experiment (→ 404 at the route)."""
    with storage.open(workspace_id) as scope:
        try:
            exp = scope.get_entity(Experiment, experiment_id)
        except Exception:
            return None
        assert isinstance(exp, Experiment)
        iterations = _experiment_iterations(scope, experiment_id)
        if not iterations:
            return ExperimentResultsResponse(experiment_id=experiment_id, iteration=None, cases=[], total=0)
        best = max(
            (it for it in iterations if it.metrics is not None),
            key=lambda it: it.metrics.primary.value,  # type: ignore[union-attr]
            default=None,
        )
        if best is None:
            return ExperimentResultsResponse(experiment_id=experiment_id, iteration=None, cases=[], total=0)
        best_iter = best.iteration
        traces_by_case: dict[str, Trace] = {}
        hot_traces = getattr(storage, "traces_for_experiment_iteration", None)
        traces = (
            hot_traces(workspace_id, experiment_id, best_iter)
            if callable(hot_traces)
            else scope.list_entities(
                Trace,
                ListFilter(where={"run.experiment_id": experiment_id, "run.iteration": best_iter}),
            )
        )
        for t in traces:
            if not isinstance(t, Trace) or t.run.eval_case_id is None:
                continue
            # First trace per case (rep 0) is representative for the grid view.
            traces_by_case.setdefault(t.run.eval_case_id, t)
        hot_cases = getattr(storage, "eval_cases_for_experiment", None)
        case_rows = (
            hot_cases(workspace_id, experiment_id)
            if callable(hot_cases)
            else scope.list_entities(EvalCase, ListFilter(where={"experiment_id": experiment_id}))
        )
        cases = {c.id: c for c in case_rows if isinstance(c, EvalCase)}

    rows: list[CaseResultRow] = []
    # Every case the experiment declared, whether or not its trace was kept.
    for case_id in sorted(set(cases) | set(traces_by_case)):
        case = cases.get(case_id)
        trace = traces_by_case.get(case_id)
        rows.append(_case_result_row(case_id, case, trace, best_iter))
    return ExperimentResultsResponse(
        experiment_id=experiment_id,
        iteration=best_iter,
        cases=rows,
        total=len(rows),
    )


def _case_result_row(
    case_id: str, case: EvalCase | None, trace: Trace | None, iteration: int
) -> CaseResultRow:
    expected = case.expected.model_dump(mode="json") if case is not None else None
    case_name = case.name if case is not None else None
    if trace is None:
        return CaseResultRow(
            case_id=case_id,
            case_name=case_name,
            iteration=iteration,
            expected=expected,
        )
    primary = trace.grader_results[0] if trace.grader_results else None
    matched = primary.label == "pass" if primary is not None else None
    failure_modes = sorted({m for gr in trace.grader_results for m in gr.failure_modes})
    return CaseResultRow(
        case_id=case_id,
        case_name=case_name,
        run_id=trace.run.run_id,
        trace_id=trace.id,
        iteration=trace.run.iteration if trace.run.iteration is not None else iteration,
        expected=expected,
        detected=_detected_from_trace(trace),
        matched=matched,
        score=primary.score if primary is not None else None,
        label=primary.label if primary is not None else None,
        failure_modes=failure_modes,
        grader_results=[gr.model_dump(mode="json") for gr in trace.grader_results],
    )


def _detected_from_trace(trace: Trace) -> dict[str, Any]:
    """What the agent produced, read off the persisted trace: the response text
    (inlined on the LLM span), the structured output, and the tools it invoked."""
    content: str | None = None
    for span in trace.spans:
        if isinstance(span, LLMCallSpan) and span.output.content_inline is not None:
            content = span.output.content_inline
            break
    tools_invoked = [s.tool_name for s in trace.spans if isinstance(s, ToolCallSpan)]
    return {
        "content": content,
        "structured_output": trace.outputs.structured_output,
        "tools_invoked": tools_invoked,
    }


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
        except Exception:
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
        except Exception:
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
        except Exception:
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


def load_trace(storage: StorageInterface, *, workspace_id: str, trace_id: str) -> TraceResponse | None:
    """Look up a Trace by either its entity id (`tr_...`) or its run_id
    (`run_...`). Both are common navigation targets — IterationRecord
    persists `run_id`s while internal storage keys by entity id."""
    experiment_name: str | None = None
    hot = getattr(storage, "trace_by_id_or_run_id", None)
    with storage.open(workspace_id) as scope:
        trace = hot(workspace_id, trace_id) if callable(hot) else None
        if trace is None:
            try:
                trace = scope.get_entity(Trace, trace_id)
            except Exception:
                trace = None
        if trace is None:
            # Fall back to a run_id lookup. The generic entities table
            # does not index json_extract, but the workspace-scoped
            # table is small enough that a single scan is fine.
            conn = _connection(storage)
            row = conn.execute(
                "SELECT payload FROM entities "
                "WHERE workspace_id = ? AND entity_type = 'Trace' "
                "AND json_extract(payload, '$.run.run_id') = ? LIMIT 1",
                (workspace_id, trace_id),
            ).fetchone()
            if row is None:
                return None
            trace = Trace.model_validate(json.loads(row[0]))
        assert isinstance(trace, Trace)
        # Resolve the human name while the scope is still open so the
        # trace viewer can title pages by experiment name (A5: identidad
        # humana sobre ULID). A missing/orphan experiment is fine —
        # standalone traces fall back to the run_id.
        if trace.run.experiment_id is not None:
            try:
                exp = scope.get_entity(Experiment, trace.run.experiment_id)
                if isinstance(exp, Experiment):
                    experiment_name = exp.name
            except Exception:
                experiment_name = None
    return TraceResponse(
        id=trace.id,
        run_id=trace.run.run_id,
        experiment_id=trace.run.experiment_id,
        experiment_name=experiment_name,
        iteration=trace.run.iteration,
        thread_id=trace.run.thread_id,
        thread_position=trace.run.thread_position,
        final_state=str(trace.final_state.status),
        started_at=trace.environment.started_at,
        ended_at=trace.environment.ended_at,
        spans=[_span_summary(s) for s in trace.spans],
        metrics=trace.metrics.model_dump(mode="json"),
    )


def load_thread(
    storage: StorageInterface, *, workspace_id: str, thread_id: str
) -> ThreadResponse | None:
    """Assemble every Trace sharing `thread_id` into an ordered conversation.

    Traces are ordered by `run.thread_position` when set, falling back to
    `environment.started_at` so a thread without explicit turn indices still
    reads in chronological order. Each turn carries its grader results so the
    thread view shows the grade per turn, not just the transcript.
    Returns None when no trace carries the thread_id.
    """
    hot = getattr(storage, "traces_by_thread_id", None)
    if callable(hot):
        traces = hot(workspace_id, thread_id)
    else:
        conn = _connection(storage)
        rows = conn.execute(
            "SELECT payload FROM entities "
            "WHERE workspace_id = ? AND entity_type = 'Trace' "
            "AND json_extract(payload, '$.run.thread_id') = ?",
            (workspace_id, thread_id),
        ).fetchall()
        traces = [Trace.model_validate(json.loads(payload)) for (payload,) in rows]
    if not traces:
        return None

    def _sort_key(t: Trace) -> tuple[int, int, datetime]:
        # Explicitly-positioned turns first (by position), then the rest by
        # start time. The leading int makes positioned turns sort ahead of
        # unpositioned ones deterministically.
        pos = t.run.thread_position
        has_pos = 0 if pos is not None else 1
        return (has_pos, pos if pos is not None else 0, t.environment.started_at)

    traces.sort(key=_sort_key)

    turns: list[ThreadTurn] = []
    for idx, trace in enumerate(traces):
        primary_grade = trace.grader_results[0].label if trace.grader_results else None
        turns.append(
            ThreadTurn(
                trace_id=trace.id,
                run_id=trace.run.run_id,
                position=trace.run.thread_position
                if trace.run.thread_position is not None
                else idx,
                experiment_id=trace.run.experiment_id,
                iteration=trace.run.iteration,
                final_state=str(trace.final_state.status),
                started_at=trace.environment.started_at,
                ended_at=trace.environment.ended_at,
                primary_grade=primary_grade,
                grader_results=[g.model_dump(mode="json") for g in trace.grader_results],
                metrics=trace.metrics.model_dump(mode="json"),
            )
        )
    return ThreadResponse(thread_id=thread_id, turn_count=len(turns), turns=turns)


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


def _experiment_summary_dict(exp: Experiment, *, iteration_count: int) -> dict[str, Any]:
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


def _iteration_summaries(
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


def _span_summary(span: Any) -> SpanSummary:
    """Project any Span subclass into the `SpanSummary` view model.

    Delegates the field selection to `trace.span_view.span_view`, the
    shared projection the live SSE path also uses, so a persisted span and
    its live twin render identically. This function only adapts that dict
    into the Pydantic model for the REST snapshot.
    """
    return SpanSummary(**span_view(span))
