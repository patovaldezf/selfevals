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
from typing import Any, cast

from pydantic import BaseModel

from selfevals.api.schemas import (
    CaseListResponse,
    CaseSummary,
    CompareFailureModes,
    CompareFunnelRow,
    CompareMetricRow,
    CompareParamRow,
    CompareRecommendation,
    CompareResponse,
    DatasetDetailResponse,
    DatasetListPage,
    DatasetStatisticsView,
    DatasetSummary,
    DetectedView,
    ExpectedView,
    ExperimentDetailResponse,
    ExperimentListPage,
    ExperimentResultsResponse,
    ExperimentSummary,
    FeatureRef,
    FunnelNodeResponse,
    FunnelResponse,
    IterationSummary,
    ScenarioResult,
    SpanSummary,
    SplitAllocationView,
    ThreadResponse,
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
from selfevals.schemas.dataset import Dataset
from selfevals.schemas.enums import DatasetStatus, ExperimentState
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


def list_workspaces(storage: StorageInterface) -> list[WorkspaceSummary]:
    """Cross-workspace listing. The storage backend exposes a dedicated
    summary method because the typed scope interface is intentionally
    scoped — no way to list without a workspace_id."""
    return cast(
        "list[WorkspaceSummary]",
        storage.list_workspace_summaries(),  # type: ignore[attr-defined]
    )


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
    experiments, total, iteration_counts = storage.list_experiments_page(  # type: ignore[attr-defined]
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
                    exp, iteration_count=iteration_counts.get(exp.id, 0)
                )
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
    cases = storage.eval_cases_for_experiment(workspace_id, experiment_id)  # type: ignore[attr-defined]
    trace_refs = storage.latest_trace_refs_by_case(workspace_id, experiment_id)  # type: ignore[attr-defined]
    cases.sort(key=lambda c: c.name)
    summaries = [_case_summary(c, trace_refs.get(c.id)) for c in cases]
    holdout_count = sum(1 for c in cases if c.holdout)
    return CaseListResponse(
        cases=summaries,
        total=len(summaries),
        holdout_count=holdout_count,
    )


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
    storage: StorageInterface,
    *,
    workspace_id: str,
    experiment_id: str,
    include_turns: bool = False,
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

    When `include_turns` is set, a conversation case (input with `messages`,
    producing one trace per turn sharing a `thread_id`) carries its turns in
    `turns`, each a `ScenarioResult` of the same shape. Off by default so the
    common case-level grid stays a single representative trace per case.

    Returns None for an unknown experiment (→ 404 at the route)."""
    with storage.open(workspace_id) as scope:
        try:
            exp = scope.get_entity(Experiment, experiment_id)
        except Exception:
            return None
        assert isinstance(exp, Experiment)
        iterations = _experiment_iterations(scope, experiment_id)
        empty = ExperimentResultsResponse(
            experiment_id=experiment_id, iteration=None, cases=[], total=0
        )
        if not iterations:
            return empty
        best = max(
            (it for it in iterations if it.metrics is not None),
            key=lambda it: it.metrics.primary.value,  # type: ignore[union-attr]
            default=None,
        )
        if best is None:
            return empty
        best_iter = best.iteration
        # All best-iteration traces, grouped by case. The first per case is the
        # representative for the case-level row; the full list feeds `turns`.
        traces_by_case: dict[str, list[Trace]] = {}
        traces = storage.traces_for_experiment_iteration(  # type: ignore[attr-defined]
            workspace_id, experiment_id, best_iter
        )
        for t in traces:
            if not isinstance(t, Trace) or t.run.eval_case_id is None:
                continue
            traces_by_case.setdefault(t.run.eval_case_id, []).append(t)
        case_rows = storage.eval_cases_for_experiment(  # type: ignore[attr-defined]
            workspace_id, experiment_id
        )
        cases = {c.id: c for c in case_rows if isinstance(c, EvalCase)}

    rows: list[ScenarioResult] = []
    # Every case the experiment declared, whether or not its trace was kept.
    for case_id in sorted(set(cases) | set(traces_by_case)):
        case = cases.get(case_id)
        case_traces = traces_by_case.get(case_id, [])
        representative = case_traces[0] if case_traces else None
        row = _scenario_result(case_id, case, representative, best_iter)
        # Expand turns only for a genuine multi-turn conversation — traces with
        # more than one distinct `thread_position`. A single-turn case (even one
        # with `messages`) or multiple repetitions of a single-shot case are NOT
        # turns, so they stay flat (no redundant `turns` duplicating the case).
        if include_turns and case is not None:
            positions = {t.run.thread_position for t in case_traces}
            if len(positions) > 1:
                row.turns = _turns_for_case(case, case_traces, best_iter)
        rows.append(row)
    return ExperimentResultsResponse(
        experiment_id=experiment_id,
        iteration=best_iter,
        cases=rows,
        total=len(rows),
    )


def _turns_for_case(
    case: EvalCase, traces: list[Trace], iteration: int
) -> list[ScenarioResult]:
    """One `ScenarioResult` per turn of a conversation case, ordered like
    `load_thread` (by `thread_position`, then `started_at`)."""

    def _sort_key(t: Trace) -> tuple[int, int, datetime]:
        pos = t.run.thread_position
        has_pos = 0 if pos is not None else 1
        return (has_pos, pos if pos is not None else 0, t.environment.started_at)

    ordered = sorted(traces, key=_sort_key)
    turns: list[ScenarioResult] = []
    for idx, trace in enumerate(ordered):
        turn = _scenario_result(case.id, case, trace, iteration)
        turn.position = trace.run.thread_position if trace.run.thread_position is not None else idx
        turns.append(turn)
    return turns


def _scenario_result(
    case_id: str, case: EvalCase | None, trace: Trace | None, iteration: int
) -> ScenarioResult:
    case_name = case.name if case is not None else None
    expected = _expected_view(case)
    if trace is None:
        # No persisted trace (e.g. it passed under persist_traces="failed").
        return ScenarioResult(
            case_id=case_id,
            case_name=case_name,
            iteration=iteration,
            expected=expected,
        )
    primary = trace.grader_results[0] if trace.grader_results else None
    matched = primary.label == "pass" if primary is not None else None
    failure_modes = sorted({m for gr in trace.grader_results for m in gr.failure_modes})
    detected, message = _detected_view(case, trace)
    return ScenarioResult(
        case_id=case_id,
        case_name=case_name,
        run_id=trace.run.run_id,
        trace_id=trace.id,
        iteration=trace.run.iteration if trace.run.iteration is not None else iteration,
        matched=matched,
        score=primary.score if primary is not None else None,
        label=primary.label if primary is not None else None,
        message=message,
        failure_modes=failure_modes,
        expected=expected,
        detected=detected,
        grader_results=[gr.model_dump(mode="json") for gr in trace.grader_results],
    )


def _trace_content(trace: Trace) -> str | None:
    """The classified message: the agent's reply text, inlined on the LLM span."""
    for span in trace.spans:
        if isinstance(span, LLMCallSpan) and span.output.content_inline is not None:
            return span.output.content_inline
    return None


def _expected_view(case: EvalCase | None) -> ExpectedView | None:
    """Project the case's declared expectations into an `ExpectedView`, carrying
    only the dimensions the case actually declares. None when nothing is declared
    (or no case on disk) — we don't fabricate empty rules."""
    if case is None:
        return None
    exp = case.expected
    view = ExpectedView(
        structured_output=exp.structured_output,
        must_include=list(exp.must_include) or None,
        must_not_include=list(exp.must_not_include) or None,
        required_tools=list(exp.required_tools) or None,
        forbidden_tools=list(exp.forbidden_tools) or None,
    )
    # All-None → nothing declared; report None rather than an empty object.
    if view.model_dump(exclude_none=True):
        return view
    return None


def _detected_view(
    case: EvalCase | None, trace: Trace
) -> tuple[DetectedView | None, str | None]:
    """Project what the agent produced into a `DetectedView` that mirrors the
    case's declared dimensions, plus the classified `message`.

    Only the dimensions the case declared are compared: a structured case gets
    `structured_output`, a substring case gets `content` + which substrings were
    `missing`/`forbidden_present` (read from the grade's failure modes), a tool
    case gets `tools_invoked`. With no declared dimensions we still return the raw
    `content`/`structured_output` so the FE has something to show."""
    content = _trace_content(trace)
    structured = trace.outputs.structured_output
    tools_invoked = [s.tool_name for s in trace.spans if isinstance(s, ToolCallSpan)]
    modes = {m for gr in trace.grader_results for m in gr.failure_modes}

    if case is None:
        view = DetectedView(content=content, structured_output=structured)
        return (view if view.model_dump(exclude_none=True) else None), content

    exp = case.expected
    kwargs: dict[str, Any] = {}
    if exp.structured_output is not None:
        kwargs["structured_output"] = structured
    if exp.must_include:
        kwargs["content"] = content
        # The grader emits a `missing_required_substring` mode when one or more
        # required substrings are absent (without naming which); surface the
        # declared set as the candidate gaps when that mode fired. Exact
        # per-substring attribution lives in the funnel breakdown for the drill-down.
        if "missing_required_substring" in modes:
            kwargs["missing"] = list(exp.must_include)
    if exp.must_not_include and "forbidden_substring" in modes:
        kwargs["content"] = content
        kwargs["forbidden_present"] = list(exp.must_not_include)
    if exp.required_tools or exp.forbidden_tools:
        kwargs["tools_invoked"] = tools_invoked
    if not kwargs:
        # Nothing declared to compare → still show the raw output.
        kwargs = {"content": content, "structured_output": structured}
    view = DetectedView(**kwargs)
    return (view if view.model_dump(exclude_none=True) else None), content


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
    trace = storage.trace_by_id_or_run_id(workspace_id, trace_id)  # type: ignore[attr-defined]
    if trace is None:
        return None
    assert isinstance(trace, Trace)
    # Resolve the human name so the trace viewer can title pages by experiment
    # name (A5: identidad humana sobre ULID). A missing/orphan experiment is
    # fine — standalone traces fall back to the run_id.
    if trace.run.experiment_id is not None:
        with storage.open(workspace_id) as scope:
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
    """Assemble every Trace sharing `thread_id` into an ordered conversation,
    each turn projected as a `ScenarioResult` — the same shape `/results` uses.

    Traces are ordered by `run.thread_position` when set, falling back to
    `environment.started_at` so a thread without explicit turn indices still
    reads in chronological order. Each turn carries its own expected/detected
    (derived from the turn's `EvalCase`) plus the classified `message`, so the
    thread view shows the per-turn expected-vs-detected diff, not just the grade.
    Returns None when no trace carries the thread_id.
    """
    traces = storage.traces_by_thread_id(workspace_id, thread_id)  # type: ignore[attr-defined]
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

    # Cross-reference each turn's EvalCase so expected/detected can be derived.
    case_ids = {t.run.eval_case_id for t in traces if t.run.eval_case_id is not None}
    cases: dict[str, EvalCase] = {}
    if case_ids:
        with storage.open(workspace_id) as scope:
            for cid in case_ids:
                try:
                    entity = scope.get_entity(EvalCase, cid)
                except Exception:
                    continue
                if isinstance(entity, EvalCase):
                    cases[cid] = entity

    turns: list[ScenarioResult] = []
    for idx, trace in enumerate(traces):
        case = cases.get(trace.run.eval_case_id) if trace.run.eval_case_id else None
        iteration = trace.run.iteration if trace.run.iteration is not None else 0
        turn = _scenario_result(trace.run.eval_case_id or trace.run.run_id, case, trace, iteration)
        turn.position = trace.run.thread_position if trace.run.thread_position is not None else idx
        turns.append(turn)
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


# --- datasets --------------------------------------------------------------


def _dataset_summary(ds: Dataset) -> DatasetSummary:
    return DatasetSummary(
        id=ds.id,
        name=ds.name,
        description=ds.description,
        dataset_type=str(ds.dataset_type),
        status=str(ds.status),
        case_count=len(ds.cases),
        manifest_hash=ds.manifest_hash,
        created_at=ds.created_at,
        updated_at=ds.updated_at,
    )


def list_datasets(
    storage: StorageInterface,
    *,
    workspace_id: str,
    limit: int = 100,
    offset: int = 0,
    status: DatasetStatus | None = None,
    dataset_type: str | None = None,
) -> DatasetListPage:
    """Paginated datasets listing, with optional status/type filters.

    Filters apply in memory before pagination so `total`/`has_more` describe
    the filtered set (same approach as `list_experiments`). Newest first.
    """
    with storage.open(workspace_id) as scope:
        datasets = [
            d
            for d in scope.list_entities(Dataset, ListFilter(order_by="created_at"))
            if isinstance(d, Dataset)
        ]
    if status is not None:
        datasets = [d for d in datasets if d.status == status]
    if dataset_type is not None:
        datasets = [d for d in datasets if str(d.dataset_type) == dataset_type]
    total = len(datasets)
    page = datasets[offset : offset + limit]
    return DatasetListPage(
        items=[_dataset_summary(d) for d in page],
        total=total,
        limit=limit,
        offset=offset,
        has_more=(offset + limit) < total,
    )


def dataset_detail(
    storage: StorageInterface, *, workspace_id: str, dataset_id: str
) -> DatasetDetailResponse | None:
    """One dataset with its resolved cases, split, and statistics.

    Returns None (the endpoint maps to 404) when the dataset is missing. Cases
    are resolved from the dataset's `EntityRef` list; refs whose EvalCase is no
    longer in storage are skipped rather than failing the whole response.
    """
    with storage.open(workspace_id) as scope:
        try:
            ds = scope.get_entity(Dataset, dataset_id)
        except Exception:
            return None
        assert isinstance(ds, Dataset)
        ref_ids = {ref.id for ref in ds.cases}
        cases = [
            c
            for c in scope.list_entities(EvalCase, ListFilter())
            if isinstance(c, EvalCase) and c.id in ref_ids
        ]
    cases.sort(key=lambda c: c.name)
    sa = ds.split_allocation
    stats = (
        DatasetStatisticsView(**ds.statistics.model_dump(mode="json"))
        if ds.statistics is not None
        else None
    )
    return DatasetDetailResponse(
        id=ds.id,
        name=ds.name,
        description=ds.description,
        dataset_type=str(ds.dataset_type),
        status=str(ds.status),
        case_count=len(ds.cases),
        manifest_hash=ds.manifest_hash,
        split_allocation=SplitAllocationView(
            optimization=sa.optimization,
            holdout=sa.holdout,
            reliability=sa.reliability,
            other=dict(sa.other),
        ),
        statistics=stats,
        cases=[_case_summary(c) for c in cases],
        created_at=ds.created_at,
        updated_at=ds.updated_at,
    )
