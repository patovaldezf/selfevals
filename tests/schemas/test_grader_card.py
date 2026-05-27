from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from selfevals.schemas.enums import GraderCardState, GroundTruthMethod
from selfevals.schemas.grader_card import (
    CalibrationMetrics,
    CalibrationThresholds,
    GraderCard,
    GraderIO,
)

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _card(**overrides: Any) -> GraderCard:
    base: dict[str, Any] = {
        "id": GraderCard.make_id(),
        "workspace_id": WS,
        "name": "exact_match_product_id",
        "purpose": "Did the agent emit the expected product_id?",
        "grader_kind": "deterministic",
        "method": GroundTruthMethod.EXACT_MATCH,
        "io": GraderIO(input_fields=["agent_response", "expected.product_id"]),
    }
    base.update(overrides)
    return GraderCard(**base)


def test_grader_card_happy_non_blocking() -> None:
    card = _card()
    assert card.blocking is False
    assert card.state == GraderCardState.CALIBRATING


def test_blocking_without_thresholds_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        _card(blocking=True)
    msg = str(exc.value)
    assert "min_precision" in msg
    assert "min_recall" in msg
    assert "max_high_risk_false_negatives" in msg


def test_blocking_with_strict_thresholds_ok() -> None:
    card = _card(
        blocking=True,
        thresholds=CalibrationThresholds(
            min_precision=0.90,
            min_recall=0.95,
            max_high_risk_false_negatives=0,
        ),
    )
    assert card.blocking is True


@pytest.mark.parametrize(
    "thresholds_kwargs",
    [
        {"min_precision": 0.89, "min_recall": 0.95, "max_high_risk_false_negatives": 0},
        {"min_precision": 0.90, "min_recall": 0.94, "max_high_risk_false_negatives": 0},
        {"min_precision": 0.90, "min_recall": 0.95, "max_high_risk_false_negatives": 1},
    ],
)
def test_blocking_thresholds_must_meet_bar(thresholds_kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _card(blocking=True, thresholds=CalibrationThresholds(**thresholds_kwargs))


def test_metrics_bounds() -> None:
    with pytest.raises(ValidationError):
        CalibrationMetrics(precision=1.01)
    with pytest.raises(ValidationError):
        CalibrationMetrics(spearman=-1.5)


def test_io_requires_at_least_one_input_field() -> None:
    with pytest.raises(ValidationError):
        GraderIO(input_fields=[])
