"""FailureMode entity + taxonomy seeding."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from selfeval.schemas.enums import FailureModeStatus
from selfeval.schemas.failure_mode import FailureMode, FailureModeExample
from selfeval.storage.seed import (
    CANONICAL_FAILURE_MODES,
    seed_failure_taxonomy,
    seed_workspace,
)
from selfeval.storage.sqlite import SQLiteStorage

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _mode(**overrides: object) -> FailureMode:
    base: dict[str, object] = {
        "id": FailureMode.make_id(),
        "workspace_id": WS,
        "slug": "invented_price",
        "title": "Invented price",
        "definition": "States a concrete price not present in the catalog.",
    }
    base.update(overrides)
    return FailureMode(**base)  # type: ignore[arg-type]


def test_failure_mode_defaults_to_candidate() -> None:
    fm = _mode()
    assert fm.status == FailureModeStatus.CANDIDATE
    assert fm.proposed_by == "seed"
    assert fm.id.startswith("fm_")


def test_slug_pattern_rejects_uppercase_and_spaces() -> None:
    with pytest.raises(ValidationError):
        _mode(slug="Invented Price")
    with pytest.raises(ValidationError):
        _mode(slug="-leading-dash")


def test_examples_round_trip() -> None:
    fm = _mode(
        examples=[FailureModeExample(trace_id="tr_1", note="quoted a price not in catalog")]
    )
    restored = FailureMode.model_validate(fm.model_dump(mode="json"))
    assert restored.examples[0].trace_id == "tr_1"
    assert restored.examples[0].note == "quoted a price not in catalog"


@pytest.fixture
def storage(tmp_path: Path) -> SQLiteStorage:
    st = SQLiteStorage(str(tmp_path / "b.sqlite"))
    seed_workspace(st, slug="w", name="w", user_id="local")
    return st


def _ws_id(st: SQLiteStorage) -> str:
    row = st.connection.execute(
        "SELECT workspace_id FROM entities WHERE entity_type = 'Workspace' LIMIT 1"
    ).fetchone()
    return str(row[0])


def test_seed_failure_taxonomy_creates_canonical_official_modes(storage: SQLiteStorage) -> None:
    ws_id = _ws_id(storage)
    modes = seed_failure_taxonomy(storage, workspace_id=ws_id)
    assert len(modes) == len(CANONICAL_FAILURE_MODES)
    assert all(m.status == FailureModeStatus.OFFICIAL for m in modes)
    slugs = {m.slug for m in modes}
    assert "hallucinated" in slugs
    assert "tool_call_wrong_tool" in slugs


def test_seed_failure_taxonomy_is_idempotent(storage: SQLiteStorage) -> None:
    ws_id = _ws_id(storage)
    first = seed_failure_taxonomy(storage, workspace_id=ws_id)
    second = seed_failure_taxonomy(storage, workspace_id=ws_id)
    # Same ids returned — nothing duplicated on re-seed.
    assert {m.id for m in first} == {m.id for m in second}
