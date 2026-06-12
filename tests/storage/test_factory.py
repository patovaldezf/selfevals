from __future__ import annotations

import os

import pytest

from selfevals.storage.factory import (
    DEFAULT_SQLITE_PATH,
    object_store_base_for_storage_url,
    open_storage,
    resolve_storage_url,
    sqlite_path_from_url,
    storage_url_label,
)
from selfevals.storage.sqlite import SQLiteStorage


def test_resolve_storage_url_defaults_to_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SELFEVALS_STORAGE_URL", raising=False)
    monkeypatch.delenv("SELFEVALS_DB", raising=False)
    assert resolve_storage_url() == DEFAULT_SQLITE_PATH


def test_storage_url_env_wins_over_sqlite_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SELFEVALS_DB", "/tmp/local.sqlite")
    monkeypatch.setenv("SELFEVALS_STORAGE_URL", "postgresql://localhost/selfevals")
    assert resolve_storage_url() == "postgresql://localhost/selfevals"


def test_sqlite_url_to_path() -> None:
    assert sqlite_path_from_url("sqlite:////tmp/selfevals.sqlite") == "/tmp/selfevals.sqlite"
    assert sqlite_path_from_url(":memory:") == ":memory:"


def test_open_storage_plain_path_is_sqlite(tmp_path: object) -> None:
    storage = open_storage(":memory:")
    try:
        assert isinstance(storage, SQLiteStorage)
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


@pytest.mark.skipif(
    not os.environ.get("SELFEVALS_TEST_POSTGRES_URL"),
    reason="requires a running Postgres database",
)
def test_postgres_storage_contract_smoke() -> None:
    from selfevals.schemas.workspace import Workspace

    storage = open_storage(os.environ["SELFEVALS_TEST_POSTGRES_URL"])
    try:
        ws = Workspace(id=Workspace.make_id(), workspace_id=Workspace.make_id(), slug="pg", name="pg")
        ws = ws.model_copy(update={"workspace_id": ws.id})
        with storage.open(ws.id) as scope:
            scope.put_entity(ws)
            got = scope.get_entity(Workspace, ws.id)
            assert isinstance(got, Workspace)
            assert got.slug == "pg"
    finally:
        storage.close()
