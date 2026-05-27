"""BaseEntity invariants: workspace_id, version, tz-aware timestamps, ids."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar

import pytest
from pydantic import ValidationError

from selfevals.schemas._base import BaseEntity, EntityRef


class _DummyEntity(BaseEntity):
    _id_prefix: ClassVar[str] = "dum"
    name: str


def _make(**overrides: object) -> _DummyEntity:
    defaults: dict[str, object] = {
        "id": _DummyEntity.make_id(),
        "workspace_id": "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ",
        "name": "thing",
    }
    defaults.update(overrides)
    return _DummyEntity(**defaults)  # type: ignore[arg-type]


def test_make_id_uses_prefix() -> None:
    new_id = _DummyEntity.make_id()
    assert new_id.startswith("dum_")


def test_workspace_id_required() -> None:
    with pytest.raises(ValidationError):
        _DummyEntity(id=_DummyEntity.make_id(), workspace_id="", name="x")


def test_workspace_id_cannot_be_omitted() -> None:
    with pytest.raises(ValidationError):
        _DummyEntity(id=_DummyEntity.make_id(), name="x")  # type: ignore[call-arg]


def test_version_defaults_to_one_and_must_be_positive() -> None:
    e = _make()
    assert e.version == 1
    with pytest.raises(ValidationError):
        _make(version=0)


def test_timestamps_are_tz_aware_utc() -> None:
    e = _make()
    assert e.created_at.tzinfo is not None
    assert e.created_at.utcoffset() == UTC.utcoffset(e.created_at)


def test_naive_timestamps_rejected() -> None:
    with pytest.raises(ValidationError):
        _make(created_at=datetime(2026, 5, 16, 12, 0, 0))


def test_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        _DummyEntity(
            id=_DummyEntity.make_id(),
            workspace_id="ws_xx",
            name="x",
            unknown_field=1,  # type: ignore[call-arg]
        )


def test_assignment_validated() -> None:
    e = _make()
    with pytest.raises(ValidationError):
        e.version = -1  # type: ignore[assignment]


def test_entity_ref_requires_id() -> None:
    with pytest.raises(ValidationError):
        EntityRef(id="")
    ref = EntityRef(id="ag_01HZZZZZZZZZZZZZZZZZZZZZZZ", version=3)
    assert ref.version == 3


def test_entity_ref_version_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        EntityRef(id="ag_xxxx", version=0)


def test_model_dump_canonical_excludes_mutable_bookkeeping() -> None:
    e = _make()
    canonical = e.model_dump_canonical()
    assert "version" not in canonical
    assert "updated_at" not in canonical
    assert "deleted_at" not in canonical
    assert canonical["name"] == "thing"
