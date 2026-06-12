"""Build an AnalysisBundle from a workspace's persisted traces.

`build_bundle` gathers the failed traces of an experiment (optionally one
iteration), projects each into the wire shape an external agent needs to do
open/axial coding, and attaches the live taxonomy the agent must classify
against. Pure read — it never mutates storage. See design §4.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from selfevals.analysis.schemas import (
    AnalysisBundle,
    BundleErrorSpan,
    BundleGrade,
    BundleMessage,
    BundleTrace,
    TaxonomyEntry,
)
from selfevals.schemas.failure_mode import FailureMode
from selfevals.schemas.trace import ErrorSpan, LLMCallSpan, ToolCallSpan, Trace
from selfevals.storage.interface import ListFilter

if TYPE_CHECKING:
    from selfevals.storage.interface import StorageInterface

# Labels that count as "needs coding". PASS/SKIPPED are dropped.
_FAILED_LABELS = {"fail", "error", "partial"}


def _transcript(trace: Trace) -> list[BundleMessage]:
    """Recover messages from the inline OTel-imported form.

    The importer stores reconstructed messages under provider_metadata
    (`selfevals.messages_in` / `selfevals.messages_out`) on each LLM span.
    We concatenate them across LLM spans in span order so the agent reads the
    real conversation, not pointers.
    """
    out: list[BundleMessage] = []
    for span in trace.spans:
        if not isinstance(span, LLMCallSpan):
            continue
        for key in ("selfevals.messages_in", "selfevals.messages_out"):
            raw = span.provider_metadata.get(key)
            if not isinstance(raw, list):
                continue
            for msg in raw:
                if isinstance(msg, dict) and "role" in msg and "content" in msg:
                    out.append(BundleMessage(role=str(msg["role"]), content=str(msg["content"])))
    return out


def _first_error_span(trace: Trace) -> BundleErrorSpan | None:
    """The first failure in the trace — Hamel's "code the first failure" rule.

    Prefers an explicit ErrorSpan; falls back to the first errored tool call.
    """
    for span in trace.spans:
        if isinstance(span, ErrorSpan):
            return BundleErrorSpan(kind=str(span.kind), name=span.name, error=span.message)
        if isinstance(span, ToolCallSpan) and span.error:
            return BundleErrorSpan(kind=str(span.kind), name=span.name, error=span.error)
    return None


def _grade(trace: Trace) -> BundleGrade:
    """Collapse the trace's grader results into one bundle grade.

    Worst label wins; deterministic failure-mode tags and any judge reason are
    surfaced so the agent focuses on the untagged residue.
    """
    severity = {"error": 4, "fail": 3, "partial": 2, "skipped": 1, "pass": 0}
    label = "pass"
    score: float | None = None
    modes: list[str] = []
    reason: str | None = None
    for gr in trace.grader_results:
        if severity.get(gr.label, 0) >= severity.get(label, 0):
            label = gr.label
            score = gr.score
        modes.extend(gr.failure_modes)
        # The judge reason is payload-routed; we pass the pointer through as a
        # hint when present (resolving it is the object store's job, optional).
        if reason is None and gr.reason_pointer:
            reason = gr.reason_pointer
    # De-dup modes preserving order.
    seen: set[str] = set()
    deduped: list[str] = []
    for m in modes:
        if m not in seen:
            seen.add(m)
            deduped.append(m)
    return BundleGrade(label=label, score=score, deterministic_modes=deduped, judge_reason=reason)


def _is_failed(trace: Trace) -> bool:
    if trace.final_state.status != "completed":
        return True
    return any(gr.label in _FAILED_LABELS for gr in trace.grader_results)


def build_bundle(
    storage: StorageInterface,
    *,
    workspace_id: str,
    experiment_id: str,
    iteration: int | None = None,
    only_failed: bool = True,
) -> AnalysisBundle:
    """Assemble the bundle. Traces are matched by experiment (and iteration)
    via the run metadata stored in each trace's payload."""
    with storage.open(workspace_id) as scope:
        trace_filter: dict[str, object] = {"run.experiment_id": experiment_id}
        if iteration is not None:
            trace_filter["run.iteration"] = iteration
        traces = [
            t
            for t in scope.list_entities(Trace, ListFilter(where=trace_filter))
            if isinstance(t, Trace)
        ]
        bundle_traces: list[BundleTrace] = []
        for trace in traces:
            if only_failed and not _is_failed(trace):
                continue
            bundle_traces.append(
                BundleTrace(
                    trace_id=trace.id,
                    run_id=trace.run.run_id,
                    thread_id=trace.run.thread_id,
                    eval_case_id=trace.run.eval_case_id,
                    grade=_grade(trace),
                    transcript=_transcript(trace),
                    first_error_span=_first_error_span(trace),
                )
            )
        taxonomy = [
            TaxonomyEntry(
                id=fm.id,
                slug=fm.slug,
                title=fm.title,
                definition=fm.definition,
                status=str(fm.status),
            )
            for fm in scope.list_entities(FailureMode)
            if isinstance(fm, FailureMode) and str(fm.status) != "retired"
        ]

    return AnalysisBundle(
        workspace_id=workspace_id,
        experiment_id=experiment_id,
        iteration=iteration,
        taxonomy=taxonomy,
        traces=bundle_traces,
    )
