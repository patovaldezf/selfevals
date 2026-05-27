from __future__ import annotations

import pytest
from pydantic import ValidationError

from selfeval.schemas.enums import Role
from selfeval.schemas.workspace import Member, Workspace


def _new_workspace(slug: str = "pato") -> Workspace:
    ws_id = Workspace.make_id()
    return Workspace(id=ws_id, workspace_id=ws_id, slug=slug, name="Patricio's workspace")


def test_workspace_self_referential_id() -> None:
    ws = _new_workspace()
    assert ws.workspace_id == ws.id


def test_workspace_id_mismatch_rejected() -> None:
    with pytest.raises(ValidationError):
        Workspace(
            id="ws_01HZZZZZZZZZZZZZZZZZZZZZZZ",
            workspace_id="ws_01HAAAAAAAAAAAAAAAAAAAAAAA",
            slug="x",
            name="x",
        )


@pytest.mark.parametrize(
    "bad_slug", ["", "-leading-dash", "_leading", "UPPER", "white space", "a" * 64]
)
def test_invalid_slugs_rejected(bad_slug: str) -> None:
    ws_id = Workspace.make_id()
    with pytest.raises(ValidationError):
        Workspace(id=ws_id, workspace_id=ws_id, slug=bad_slug, name="x")


@pytest.mark.parametrize("good_slug", ["pato", "p", "ws-1", "ws_1", "seals-prod", "a" * 63])
def test_valid_slugs_accepted(good_slug: str) -> None:
    ws_id = Workspace.make_id()
    ws = Workspace(id=ws_id, workspace_id=ws_id, slug=good_slug, name="x")
    assert ws.slug == good_slug


def test_member_requires_role_from_enum() -> None:
    ws = _new_workspace()
    member = Member(
        id=Member.make_id(),
        workspace_id=ws.id,
        user_id="patovaldezflores@gmail.com",
        role=Role.ADMIN,
    )
    assert member.role == Role.ADMIN


def test_member_role_invalid_rejected() -> None:
    ws = _new_workspace()
    with pytest.raises(ValidationError):
        Member(
            id=Member.make_id(),
            workspace_id=ws.id,
            user_id="x",
            role="owner",  # type: ignore[arg-type]
        )


def test_workspace_retention_bounds() -> None:
    ws_id = Workspace.make_id()
    with pytest.raises(ValidationError):
        Workspace(
            id=ws_id,
            workspace_id=ws_id,
            slug="x",
            name="x",
            settings={"retention_days": 0},  # type: ignore[arg-type]
        )
