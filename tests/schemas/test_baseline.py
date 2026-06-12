"""Round-trip + invariants for BaselineRecord."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from selfevals.schemas.baseline import BaselineRecord

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _record(**overrides: object) -> BaselineRecord:
    base: dict[str, object] = {
        "id": BaselineRecord.make_id(),
        "workspace_id": WS,
        "experiment_id": "exp_x",
        "iteration_id": "itr_x",
        "iteration": 3,
        "primary_metric": "pass@1",
        "primary_value": 0.85,
    }
    base.update(overrides)
    return BaselineRecord(**base)


def test_id_prefix() -> None:
    record = _record()
    assert record.id.startswith("bl_")


def test_json_round_trip() -> None:
    record = _record(macro_f1=0.72, error_rate=0.1, note="frozen v1")
    rebuilt = BaselineRecord.model_validate_json(record.model_dump_json())
    assert rebuilt == record


def test_optional_fields_default() -> None:
    record = _record()
    assert record.macro_f1 is None
    assert record.error_rate == 0.0
    assert record.note is None


def test_error_rate_bounds() -> None:
    with pytest.raises(ValidationError):
        _record(error_rate=1.5)


def test_iteration_non_negative() -> None:
    with pytest.raises(ValidationError):
        _record(iteration=-1)
