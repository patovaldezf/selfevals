"""Per-scenario results, trace lookup, and thread assembly."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from selfevals.api.queries._shared import span_summary
from selfevals.api.schemas import (
    DetectedView,
    ExpectedView,
    ExperimentResultsResponse,
    ScenarioResult,
    ThreadResponse,
    TraceResponse,
)
from selfevals.cli.commands import _experiment_iterations
from selfevals.schemas.eval_case import EvalCase
from selfevals.schemas.experiment import Experiment
from selfevals.schemas.trace import LLMCallSpan, ToolCallSpan, Trace
from selfevals.storage.errors import EntityNotFoundError
from selfevals.storage.interface import StorageInterface


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
        except EntityNotFoundError:
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
        traces = storage.traces_for_experiment_iteration(workspace_id, experiment_id, best_iter)
        for t in traces:
            if not isinstance(t, Trace) or t.run.eval_case_id is None:
                continue
            traces_by_case.setdefault(t.run.eval_case_id, []).append(t)
        case_rows = storage.eval_cases_for_experiment(workspace_id, experiment_id)
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


def _turns_for_case(case: EvalCase, traces: list[Trace], iteration: int) -> list[ScenarioResult]:
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
        started_at=trace.environment.started_at,
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


def _detected_view(case: EvalCase | None, trace: Trace) -> tuple[DetectedView | None, str | None]:
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


def load_trace(
    storage: StorageInterface, *, workspace_id: str, trace_id: str
) -> TraceResponse | None:
    """Look up a Trace by either its entity id (`tr_...`) or its run_id
    (`run_...`). Both are common navigation targets — IterationRecord
    persists `run_id`s while internal storage keys by entity id."""
    experiment_name: str | None = None
    trace = storage.trace_by_id_or_run_id(workspace_id, trace_id)
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
            except EntityNotFoundError:
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
        spans=[span_summary(s) for s in trace.spans],
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
    traces = storage.traces_by_thread_id(workspace_id, thread_id)
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
                except EntityNotFoundError:
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
