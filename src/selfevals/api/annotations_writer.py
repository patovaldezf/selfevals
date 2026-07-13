"""Write/read path for trace annotations — human "good/bad" feedback on a run.

An `Annotation` records one person's verdict + notes on a trace (the entity and
its Postgres mapper already existed; this is the missing API surface). The
verdict is stored under `labels.data["verdict"]` so the free-form label schema
stays intact and a future rubric registry can layer on top.

A trace usually has a source `eval_case_id` (it was run from a case); we stamp
that as the annotation's `case_id`. Ad-hoc/production traces with no source case
get a sentinel `case_id` so the annotation still persists (the entity requires a
non-empty `case_id`); the `trace_id` is always the real link.
"""

from __future__ import annotations

from selfevals.api.queries._shared import resolve_trace
from selfevals.api.schemas import (
    AnnotationListResponse,
    AnnotationView,
    CreateAnnotationRequest,
)
from selfevals.schemas.annotation import Annotation, AnnotationLabels
from selfevals.storage.interface import ListFilter, StorageInterface

# case_id sentinel for a trace with no originating EvalCase (ad-hoc / production).
_ADHOC_CASE_ID = "ec_adhoc"


class AnnotationError(ValueError):
    """Bad annotation request (maps to 4xx)."""


def _to_view(ann: Annotation) -> AnnotationView:
    verdict = ann.labels.data.get("verdict")
    return AnnotationView(
        id=ann.id,
        trace_id=ann.trace_id,
        case_id=ann.case_id,
        annotator_id=ann.annotator_id,
        verdict=verdict if isinstance(verdict, str) else None,
        notes=ann.notes,
        confidence=ann.confidence,
        flagged_for_adjudication=ann.flagged_for_adjudication,
        created_at=ann.created_at.isoformat(),
    )


def create_annotation(
    storage: StorageInterface,
    *,
    workspace_id: str,
    trace_id: str,
    body: CreateAnnotationRequest,
    user_id: str | None,
) -> AnnotationView:
    """Persist a human verdict + notes on a trace. Returns the stored view."""
    annotator = body.annotator_id or user_id or "human"
    with storage.open(workspace_id) as scope:
        trace = resolve_trace(scope, trace_id)  # raises EntityNotFoundError → 404 upstream
        case_id = trace.run.eval_case_id or _ADHOC_CASE_ID
        annotation = Annotation(
            id=Annotation.make_id(),
            workspace_id=workspace_id,
            case_id=case_id,
            trace_id=trace.id,
            annotator_id=annotator,
            labels=AnnotationLabels(data={"verdict": body.verdict}),
            notes=body.notes,
            confidence=body.confidence,
            flagged_for_adjudication=body.flagged_for_adjudication,
        )
        scope.put_entity(annotation)
    return _to_view(annotation)


def list_annotations_for_trace(
    storage: StorageInterface,
    *,
    workspace_id: str,
    trace_id: str,
) -> AnnotationListResponse:
    """All annotations attached to a trace, newest first."""
    with storage.open(workspace_id) as scope:
        trace = resolve_trace(scope, trace_id)
        anns = scope.list_entities(
            Annotation,
            ListFilter(where={"trace_id": trace.id}, order_by="created_at", order_desc=True),
        )
    views = [_to_view(a) for a in anns if isinstance(a, Annotation)]
    return AnnotationListResponse(annotations=views)


def list_annotations_for_case(
    storage: StorageInterface,
    *,
    workspace_id: str,
    case_id: str,
) -> AnnotationListResponse:
    """All annotations attached to a case (across its traces), newest first."""
    with storage.open(workspace_id) as scope:
        anns = scope.list_entities(
            Annotation,
            ListFilter(where={"case_id": case_id}, order_by="created_at", order_desc=True),
        )
    views = [_to_view(a) for a in anns if isinstance(a, Annotation)]
    return AnnotationListResponse(annotations=views)
