from __future__ import annotations

from pathlib import Path

from bootstrap.schemas.enums import Role
from bootstrap.storage.seed import seed_workspace
from bootstrap.storage.sqlite import SQLiteStorage


def test_seed_creates_workspace_and_all_roles(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "test.db")
    result = seed_workspace(
        storage,
        slug="pato",
        name="Patricio's workspace",
        user_id="patovaldezflores@gmail.com",
    )
    ws = result.workspace
    assert ws.slug == "pato"
    assert ws.owner_id == "patovaldezflores@gmail.com"
    assert {m.role for m in result.members} == set(Role)
    storage.close()


def test_seed_is_idempotent(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "test.db")
    first = seed_workspace(
        storage,
        slug="pato",
        name="x",
        user_id="p@example.com",
    )
    second = seed_workspace(
        storage,
        slug="pato",
        name="renamed (ignored)",
        user_id="p@example.com",
    )
    assert first.workspace.id == second.workspace.id
    assert {m.role for m in second.members} == set(Role)
    storage.close()


def test_seed_admin_only(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "test.db")
    result = seed_workspace(
        storage,
        slug="lean",
        name="lean",
        user_id="x@x.com",
        assign_all_roles=False,
    )
    assert [m.role for m in result.members] == [Role.ADMIN]
    storage.close()


def test_seed_writes_through_workspace_scope(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "test.db")
    result = seed_workspace(
        storage, slug="pato", name="x", user_id="p@example.com"
    )
    # Re-open scope and verify we can list members.
    with storage.open(result.workspace.id) as scope:
        from bootstrap.schemas.workspace import Member, Workspace

        ws = scope.get_entity(Workspace, result.workspace.id)
        assert isinstance(ws, Workspace)
        members = scope.list_entities(Member)
        assert len(members) == 6  # all roles
    storage.close()
