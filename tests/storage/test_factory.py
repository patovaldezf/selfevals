from __future__ import annotations

import pytest

from selfevals.storage.factory import (
    object_store_base_for_storage_url,
    open_storage,
    resolve_storage_url,
    storage_url_label,
)
from selfevals.storage.postgres import PostgresStorage


def test_resolve_storage_url_arg_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SELFEVALS_STORAGE_URL", "postgresql://env/selfevals")
    assert resolve_storage_url("postgresql://arg/selfevals") == "postgresql://arg/selfevals"


def test_resolve_storage_url_falls_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SELFEVALS_STORAGE_URL", "postgresql://localhost/selfevals")
    assert resolve_storage_url() == "postgresql://localhost/selfevals"


def test_resolve_storage_url_unset_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SELFEVALS_STORAGE_URL", raising=False)
    with pytest.raises((RuntimeError, ValueError), match="SELFEVALS_STORAGE_URL"):
        resolve_storage_url()


def test_open_storage_returns_postgres(db_url: str) -> None:
    storage = open_storage(db_url)
    try:
        assert isinstance(storage, PostgresStorage)
    finally:
        storage.close()


def test_storage_url_label_redacts_postgres_credentials() -> None:
    label = storage_url_label("postgresql://user:secret@db.example.com:5432/selfevals")
    assert label == "postgresql://db.example.com/selfevals"
    assert "secret" not in label


def test_object_store_base_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SELFEVALS_OBJECTS_DIR", "/tmp/selfevals-objects")
    assert str(object_store_base_for_storage_url("postgresql://localhost/selfevals")) == (
        "/tmp/selfevals-objects"
    )


def test_object_store_base_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from pathlib import Path

    monkeypatch.delenv("SELFEVALS_OBJECTS_DIR", raising=False)
    assert object_store_base_for_storage_url("postgresql://localhost/selfevals") == Path("./objects")


def test_postgres_storage_contract_smoke(storage: object) -> None:
    """Round-trip a Workspace through the real Postgres backend.

    Uses the per-test ``storage`` fixture (an isolated Postgres database), so it
    runs whenever the test Postgres is reachable and never collides with other
    tests on unique constraints.
    """
    from selfevals.schemas.workspace import Workspace

    ws_id = Workspace.make_id()
    ws = Workspace(id=ws_id, workspace_id=ws_id, slug="pg", name="pg")
    with storage.open(ws.id) as scope:  # type: ignore[attr-defined]
        scope.put_entity(ws)
        got = scope.get_entity(Workspace, ws.id)
        assert isinstance(got, Workspace)
        assert got.slug == "pg"
