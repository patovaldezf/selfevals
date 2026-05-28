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
from typing import Any

from pydantic import BaseModel

from selfevals.api.schemas import (
    ExperimentDetailResponse,
    ExperimentListPage,
    ExperimentSummary,
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
from selfevals.schemas.experiment import Experiment
from selfevals.schemas.iteration import DecisionRecord, IterationRecord
from selfevals.schemas.trace import Trace
from selfevals.schemas.workspace import Workspace
from selfevals.storage.interface import ListFilter
from selfevals.storage.sqlite import SQLiteStorage


class AnchorPoint(BaseModel):
    experiment_id: str
    experiment_name: str
    iteration: int
    primary_metric_name: str
    primary_metric_value: float
    decision_outcome: str
    created_at: str


def list_workspaces(storage: SQLiteStorage) -> list[WorkspaceSummary]:
    """Cross-workspace listing. Direct SQL because the typed interface is
    intentionally scoped — no way to list without a workspace_id."""
    rows = storage.connection.execute(
        "SELECT payload FROM entities WHERE entity_type = 'Workspace' ORDER BY created_at DESC"
    ).fetchall()
    summaries: list[WorkspaceSummary] = []
    for (payload,) in rows:
        ws = Workspace.model_validate(json.loads(payload))
        exp_count = storage.connection.execute(
            "SELECT COUNT(1) FROM entities WHERE entity_type = 'Experiment' AND workspace_id = ?",
            (ws.id,),
        ).fetchone()[0]
        last_run = storage.connection.execute(
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


def workspace_detail(storage: SQLiteStorage, *, workspace_id: str) -> WorkspaceResponse | None:
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
    storage: SQLiteStorage,
    *,
    workspace_id: str,
    limit: int = 100,
    offset: int = 0,
) -> ExperimentListPage:
    """Paginated experiments listing (A8).

    `ListFilter` already supports limit/offset; we expose it here and
    return a `total` so the FE can show "X of N" without a second
    round-trip. The iteration-count subquery is intentionally a
    full-scan per experiment — at the volumes we're targeting
    (Fase A: <100 experiments), this is correct-and-cheap.
    """
    with storage.open(workspace_id) as scope:
        all_experiments = [
            e
            for e in scope.list_entities(Experiment, ListFilter(order_by="updated_at"))
            if isinstance(e, Experiment)
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
    storage: SQLiteStorage, *, workspace_id: str, experiment_id: str
) -> ExperimentDetailResponse | None:
    with storage.open(workspace_id) as scope:
        try:
            exp = scope.get_entity(Experiment, experiment_id)
        except Exception:
            return None
        assert isinstance(exp, Experiment)
        iterations = _experiment_iterations(scope, exp.id)
        decisions = _experiment_decisions(scope, exp.id)

    result_dict: dict[str, Any] | None = None
    if iterations:
        result = _reconstruct_result(exp, iterations, decisions)
        result_dict = json.loads(render_json(result))

    summary = ExperimentSummary(**_experiment_summary_dict(exp, iteration_count=len(iterations)))
    return ExperimentDetailResponse(
        summary=summary,
        result=result_dict,
        iterations=_iteration_summaries(iterations, decisions),
    )


def experiment_iterations(
    storage: SQLiteStorage, *, workspace_id: str, experiment_id: str
) -> list[IterationSummary]:
    with storage.open(workspace_id) as scope:
        iterations = _experiment_iterations(scope, experiment_id)
        decisions = _experiment_decisions(scope, experiment_id)
    return _iteration_summaries(iterations, decisions)


def experiment_decisions(
    storage: SQLiteStorage, *, workspace_id: str, experiment_id: str
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
    storage: SQLiteStorage, *, workspace_id: str, iteration_id: str
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


def load_trace(storage: SQLiteStorage, *, workspace_id: str, trace_id: str) -> TraceResponse | None:
    """Look up a Trace by either its entity id (`tr_...`) or its run_id
    (`run_...`). Both are common navigation targets — IterationRecord
    persists `run_id`s while internal storage keys by entity id."""
    experiment_name: str | None = None
    with storage.open(workspace_id) as scope:
        try:
            trace = scope.get_entity(Trace, trace_id)
        except Exception:
            trace = None
        if trace is None:
            # Fall back to a run_id lookup. The generic entities table
            # does not index json_extract, but the workspace-scoped
            # table is small enough that a single scan is fine.
            row = storage.connection.execute(
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
    storage: SQLiteStorage, *, workspace_id: str, thread_id: str
) -> ThreadResponse | None:
    """Assemble every Trace sharing `thread_id` into an ordered conversation.

    Traces are ordered by `run.thread_position` when set, falling back to
    `environment.started_at` so a thread without explicit turn indices still
    reads in chronological order. Each turn carries its grader results so the
    thread view shows the grade per turn, not just the transcript.
    Returns None when no trace carries the thread_id.
    """
    rows = storage.connection.execute(
        "SELECT payload FROM entities "
        "WHERE workspace_id = ? AND entity_type = 'Trace' "
        "AND json_extract(payload, '$.run.thread_id') = ?",
        (workspace_id, thread_id),
    ).fetchall()
    if not rows:
        return None

    traces = [Trace.model_validate(json.loads(payload)) for (payload,) in rows]

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
                position=trace.run.thread_position if trace.run.thread_position is not None else idx,
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


def anchor_set_history(storage: SQLiteStorage, *, workspace_id: str) -> list[AnchorPoint]:
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
    """Project any Span subclass into the trimmed view shape.

    We surface kind + name + parent + timing on every span, and copy
    the kind-specific high-value fields into `detail` for the trace
    inspector to render without fetching the full payload.
    """
    detail: dict[str, Any] = {}
    payload = span.model_dump(mode="json")
    keep_keys = {
        "provider",
        "model",
        "params",
        "tokens",
        "cost_usd",
        # Performance facets the tree node surfaces (A6). Jensen: TTFT
        # and throughput are first-class for an agent debugger; hiding
        # them in detail makes the tree blind to "fast but wrong".
        "time_to_first_token_ms",
        "tokens_per_second",
        "cache_hit",
        "retries",
        "output",
        "reasoning",
        "tool_name",
        "tool_use_id",
        "status",
        "error",
        "retriever",
        "top_k_requested",
        "top_k_returned",
        "retrieved",
        "decision_type",
        "chosen",
        "alternatives_considered",
        "guardrail",
        "passed",
        "error_type",
        "message",
        "recoverable",
        # Pointer fields per kind (schemas/trace.py). The FE resolves these
        # lazily via /payloads — without exposing them here, the trace
        # viewer can never load the actual prompt/args/result bytes, even
        # though the bytes are sitting in the object store.
        "system_prompt_pointer",
        "system_prompt_hash",
        "messages_pointer",
        "messages_hash",
        "tools_offered",
        "tools_offered_hash",
        "args_pointer",
        "args_hash",
        "result_pointer",
        "result_hash",
        "query_pointer",
        "query_hash",
        "values_pointer",
        "values_hash",
    }
    for key, value in payload.items():
        if key in keep_keys:
            detail[key] = value
    return SpanSummary(
        id=span.id,
        parent_id=span.parent_id,
        kind=str(span.kind),
        name=span.name,
        started_at=span.started_at,
        duration_ms=span.duration_ms,
        detail=detail,
    )
