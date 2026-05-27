from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from selfeval.schemas.annotation import Annotation, AnnotationLabels

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def test_annotation_minimal() -> None:
    a = Annotation(
        id=Annotation.make_id(),
        workspace_id=WS,
        case_id="ec_x",
        annotator_id="patricio",
    )
    assert a.confidence == 1.0
    assert a.flagged_for_adjudication is False
    assert a.labels.rubric_version is None


def test_annotation_with_free_labels() -> None:
    labels = AnnotationLabels(
        rubric_version="product_resolution_v1",
        data={"correctness": "pass", "tone": "warm", "notes": "good"},
    )
    a = Annotation(
        id=Annotation.make_id(),
        workspace_id=WS,
        case_id="ec_x",
        trace_id="tr_x",
        annotator_id="patricio",
        labels=labels,
        confidence=0.8,
    )
    assert a.labels.data["correctness"] == "pass"


def test_annotation_confidence_bounds() -> None:
    with pytest.raises(ValidationError):
        Annotation(
            id=Annotation.make_id(),
            workspace_id=WS,
            case_id="ec_x",
            annotator_id="x",
            confidence=1.5,
        )


def test_annotation_temporal_ordering_enforced() -> None:
    started = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
    with pytest.raises(ValidationError):
        Annotation(
            id=Annotation.make_id(),
            workspace_id=WS,
            case_id="ec_x",
            annotator_id="x",
            started_at=started,
            submitted_at=started - timedelta(seconds=1),
        )


def test_annotation_temporal_ordering_ok() -> None:
    started = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
    a = Annotation(
        id=Annotation.make_id(),
        workspace_id=WS,
        case_id="ec_x",
        annotator_id="x",
        started_at=started,
        submitted_at=started + timedelta(seconds=30),
        duration_seconds=30.0,
    )
    assert a.submitted_at is not None
