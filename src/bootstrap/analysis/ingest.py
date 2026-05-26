"""Ingest an AnalysisResult: persist assignments, candidates, hypotheses.

This is the push half of the handshake (design §4). It enforces the two
invariants that keep the taxonomy trustworthy:

  1. Each assignment targets exactly one of an existing `mode_id` (classify) or
     a `new_mode_slug` (propose) — the XOR, validated on the wire model and
     re-checked here against what actually exists.
  2. Classify-don't-rename: an assignment may reference an existing mode but can
     never edit its title/definition. New modes arrive only as candidates.
     Renaming is a separate human action. This is what keeps mode identity
     stable across analysis runs.

New modes are created idempotent on slug (a repeat slug updates the existing
candidate's examples rather than duplicating). Hypotheses are stored as
`Proposal` seeds linked to the experiment; they are not auto-run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from bootstrap.analysis.schemas import AnalysisResult
from bootstrap.schemas.enums import FailureModeStatus
from bootstrap.schemas.failure_mode import FailureMode, FailureModeExample
from bootstrap.schemas.trace import GraderResult, Trace

if TYPE_CHECKING:
    from bootstrap.storage.interface import ObjectStoreInterface, WorkspaceScope
    from bootstrap.storage.sqlite import SQLiteStorage


class AnalysisIngestError(ValueError):
    """Raised when an AnalysisResult cannot be applied (unknown ids, etc.)."""


@dataclass
class IngestSummary:
    created_candidates: list[str] = field(default_factory=list)  # fm ids
    updated_candidates: list[str] = field(default_factory=list)  # fm ids (slug re-seen)
    assignments_applied: int = 0
    hypotheses_recorded: int = 0


def ingest_result(
    storage: SQLiteStorage,
    *,
    workspace_id: str,
    experiment_id: str,
    result: AnalysisResult,
    proposed_by: str = "agent:unknown",
    object_store: ObjectStoreInterface | None = None,
) -> IngestSummary:
    """Apply an AnalysisResult to the workspace. Best-effort transactional:
    everything is validated before any write, so a bad result rejects whole."""
    summary = IngestSummary()

    with storage.open(workspace_id) as scope:
        # --- load current taxonomy, indexed both ways ---
        existing = [fm for fm in scope.list_entities(FailureMode) if isinstance(fm, FailureMode)]
        by_id = {fm.id: fm for fm in existing}
        by_slug = {fm.slug: fm for fm in existing}

        # --- validate before writing (transactional intent) ---
        for a in result.assignments:
            if a.mode_id is not None and a.mode_id not in by_id:
                raise AnalysisIngestError(f"assignment references unknown mode_id {a.mode_id!r}")
        proposed_slugs = {p.slug for p in result.proposed_modes}
        for a in result.assignments:
            # An assignment can name a new slug only if it is declared in
            # proposed_modes (so it has a definition) or already known.
            if (
                a.new_mode_slug is not None
                and a.new_mode_slug not in proposed_slugs
                and a.new_mode_slug not in by_slug
            ):
                raise AnalysisIngestError(
                    f"assignment proposes new_mode_slug {a.new_mode_slug!r} "
                    "but it is neither in proposed_modes nor already known"
                )

        # --- 1. create / update candidate modes ---
        slug_to_mode: dict[str, FailureMode] = dict(by_slug)
        for p in result.proposed_modes:
            if p.slug in by_slug:
                # Slug re-seen: keep the existing mode (classify-don't-rename),
                # only note that the agent re-proposed it.
                summary.updated_candidates.append(by_slug[p.slug].id)
                slug_to_mode[p.slug] = by_slug[p.slug]
                continue
            parent_id = by_slug[p.parent_slug].id if p.parent_slug in by_slug else None
            mode = FailureMode(
                id=FailureMode.make_id(),
                workspace_id=workspace_id,
                slug=p.slug,
                title=p.title,
                definition=p.definition,
                status=FailureModeStatus.CANDIDATE,
                parent_mode_id=parent_id,
                proposed_by=proposed_by,
            )
            scope.put_entity(mode)
            slug_to_mode[p.slug] = mode
            by_id[mode.id] = mode
            summary.created_candidates.append(mode.id)

        # --- 2. apply assignments: stamp mode id on the trace, add example ---
        for a in result.assignments:
            resolved_id = (
                a.mode_id if a.mode_id is not None else slug_to_mode[a.new_mode_slug].id  # type: ignore[index]
            )
            trace = scope.get_entity(Trace, a.trace_id)
            assert isinstance(trace, Trace)
            _stamp_mode_on_trace(trace, resolved_id, grader="error_analysis")
            scope.put_entity(trace)

            # Append example evidence to the mode (payload-route the quote).
            mode = by_id[resolved_id]
            quote_pointer = None
            quote_hash = None
            if a.quote and object_store is not None:
                from bootstrap.trace.payload_router import PayloadRouter

                router = PayloadRouter(object_store, workspace_id=workspace_id)
                routed = router.route_value(f"fm_example:{a.trace_id}", a.quote)
                quote_pointer = routed.pointer
                quote_hash = routed.content_hash
            mode.examples.append(
                FailureModeExample(
                    trace_id=a.trace_id,
                    quote_pointer=quote_pointer,
                    quote_hash=quote_hash,
                    note=a.open_note,
                )
            )
            scope.put_entity(mode)
            summary.assignments_applied += 1

        # --- 3. record hypotheses as proposal seeds (not auto-run) ---
        summary.hypotheses_recorded = _record_hypotheses(
            scope, workspace_id=workspace_id, experiment_id=experiment_id, result=result
        )

    return summary


def _stamp_mode_on_trace(trace: Trace, mode_id: str, *, grader: str) -> None:
    """Add `mode_id` to the trace's grader results without duplicating.

    If an error-analysis GraderResult already exists, extend it; otherwise add
    one carrying the trace's worst label so the link has context.
    """
    for gr in trace.grader_results:
        if gr.grader == grader:
            if mode_id not in gr.failure_modes:
                gr.failure_modes = [*gr.failure_modes, mode_id]
            return
    worst = "fail"
    for gr in trace.grader_results:
        if gr.label in {"error", "fail", "partial"}:
            worst = gr.label
            break
    trace.grader_results.append(GraderResult(grader=grader, label=worst, failure_modes=[mode_id]))


def _record_hypotheses(
    scope: WorkspaceScope,
    *,
    workspace_id: str,
    experiment_id: str,
    result: AnalysisResult,
) -> int:
    """Persist hypotheses as HypothesisRecord seeds for the proposer.

    Kept as a thin entity so the proposer (and a future llm_proposer) can
    consult them. We do not run them here.
    """
    from bootstrap.analysis.hypothesis import HypothesisRecord

    count = 0
    for h in result.hypotheses:
        scope.put_entity(
            HypothesisRecord(
                id=HypothesisRecord.make_id(),
                workspace_id=workspace_id,
                experiment_id=experiment_id,
                targets_mode_slug=h.targets_mode_slug,
                statement=h.statement,
                suggested_parameters=dict(h.suggested_parameters),
            )
        )
        count += 1
    return count
