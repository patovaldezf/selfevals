"""Workspace membership management.

Before these endpoints existed a workspace was permanently limited to whoever
created it: the only way to add a second person was an INSERT by hand. These
tests pin the two properties that make the surface safe — only admins can grant
access, and a workspace can never be left without one.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from selfevals.api.app import build_app
from selfevals.storage.factory import open_storage
from selfevals.storage.seed import seed_workspace

_ADMIN = "admin-user"
_HEADERS = {"X-SelfEvals-User": _ADMIN}


@pytest.fixture
def workspace_id(db_url: str) -> str:
    """A workspace whose creator is admin — the shape `POST /api/workspaces` makes."""
    storage = open_storage(db_url)
    try:
        seeded = seed_workspace(
            storage, slug="members-test", name="Members", user_id=_ADMIN, assign_all_roles=False
        )
        return str(seeded.workspace.id)
    finally:
        storage.close()


@pytest.fixture
def client(db_url: str, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("SELFEVALS_AUTH_MODE", "header")
    return TestClient(build_app(db_path=db_url))


class TestListAndInvite:
    def test_creator_starts_as_the_only_admin(
        self, client: TestClient, workspace_id: str
    ) -> None:
        response = client.get(f"/api/workspaces/{workspace_id}/members", headers=_HEADERS)
        assert response.status_code == 200
        members = response.json()
        assert [(m["user_id"], m["role"]) for m in members] == [(_ADMIN, "admin")]

    def test_admin_can_invite(self, client: TestClient, workspace_id: str) -> None:
        response = client.post(
            f"/api/workspaces/{workspace_id}/members",
            json={"user_id": "teammate", "role": "experimenter"},
            headers=_HEADERS,
        )
        assert response.status_code == 201, response.text
        assert response.json()["invited_by"] == _ADMIN

        # The invitee can now actually reach the workspace.
        listed = client.get(
            f"/api/workspaces/{workspace_id}/members",
            headers={"X-SelfEvals-User": "teammate"},
        )
        assert listed.status_code == 200

    def test_inviting_a_role_twice_conflicts(
        self, client: TestClient, workspace_id: str
    ) -> None:
        body = {"user_id": "teammate", "role": "viewer"}
        client.post(f"/api/workspaces/{workspace_id}/members", json=body, headers=_HEADERS)
        again = client.post(
            f"/api/workspaces/{workspace_id}/members", json=body, headers=_HEADERS
        )
        assert again.status_code == 409

    def test_a_user_may_hold_several_roles(
        self, client: TestClient, workspace_id: str
    ) -> None:
        """One row per (user, role) — the schema's uniqueness contract."""
        for role in ("viewer", "experimenter"):
            response = client.post(
                f"/api/workspaces/{workspace_id}/members",
                json={"user_id": "teammate", "role": role},
                headers=_HEADERS,
            )
            assert response.status_code == 201


class TestAuthorization:
    def test_non_admin_cannot_invite(self, client: TestClient, workspace_id: str) -> None:
        """An experimenter who could grant roles could promote themselves."""
        client.post(
            f"/api/workspaces/{workspace_id}/members",
            json={"user_id": "teammate", "role": "experimenter"},
            headers=_HEADERS,
        )
        response = client.post(
            f"/api/workspaces/{workspace_id}/members",
            json={"user_id": "outsider", "role": "admin"},
            headers={"X-SelfEvals-User": "teammate"},
        )
        assert response.status_code == 403

    def test_stranger_cannot_read_members(
        self, client: TestClient, workspace_id: str
    ) -> None:
        response = client.get(
            f"/api/workspaces/{workspace_id}/members",
            headers={"X-SelfEvals-User": "stranger"},
        )
        assert response.status_code == 403


class TestRoleChangesAndRemoval:
    def _invite(self, client: TestClient, workspace_id: str, role: str = "viewer") -> str:
        response = client.post(
            f"/api/workspaces/{workspace_id}/members",
            json={"user_id": "teammate", "role": role},
            headers=_HEADERS,
        )
        return str(response.json()["id"])

    def test_admin_can_change_a_role(self, client: TestClient, workspace_id: str) -> None:
        member_id = self._invite(client, workspace_id)
        response = client.patch(
            f"/api/workspaces/{workspace_id}/members/{member_id}",
            json={"role": "maintainer"},
            headers=_HEADERS,
        )
        assert response.status_code == 200
        assert response.json()["role"] == "maintainer"

    def test_removing_a_member_revokes_access(
        self, client: TestClient, workspace_id: str
    ) -> None:
        member_id = self._invite(client, workspace_id, role="experimenter")
        teammate = {"X-SelfEvals-User": "teammate"}
        assert client.get(f"/api/workspaces/{workspace_id}/members", headers=teammate).status_code == 200

        removed = client.delete(
            f"/api/workspaces/{workspace_id}/members/{member_id}", headers=_HEADERS
        )
        assert removed.status_code == 204
        assert client.get(f"/api/workspaces/{workspace_id}/members", headers=teammate).status_code == 403

    def test_cannot_remove_the_last_admin(
        self, client: TestClient, workspace_id: str
    ) -> None:
        """Nothing can restore an admin, so a workspace without one is stuck forever."""
        members = client.get(f"/api/workspaces/{workspace_id}/members", headers=_HEADERS).json()
        admin_row = next(m for m in members if m["role"] == "admin")

        response = client.delete(
            f"/api/workspaces/{workspace_id}/members/{admin_row['id']}", headers=_HEADERS
        )
        assert response.status_code == 409
        assert "last admin" in response.json()["detail"]

    def test_cannot_demote_the_last_admin(
        self, client: TestClient, workspace_id: str
    ) -> None:
        members = client.get(f"/api/workspaces/{workspace_id}/members", headers=_HEADERS).json()
        admin_row = next(m for m in members if m["role"] == "admin")

        response = client.patch(
            f"/api/workspaces/{workspace_id}/members/{admin_row['id']}",
            json={"role": "viewer"},
            headers=_HEADERS,
        )
        assert response.status_code == 409

    def test_last_admin_can_step_down_once_another_exists(
        self, client: TestClient, workspace_id: str
    ) -> None:
        """The guard blocks the unrecoverable case, not handover."""
        client.post(
            f"/api/workspaces/{workspace_id}/members",
            json={"user_id": "successor", "role": "admin"},
            headers=_HEADERS,
        )
        members = client.get(f"/api/workspaces/{workspace_id}/members", headers=_HEADERS).json()
        original = next(m for m in members if m["user_id"] == _ADMIN and m["role"] == "admin")

        response = client.delete(
            f"/api/workspaces/{workspace_id}/members/{original['id']}", headers=_HEADERS
        )
        assert response.status_code == 204

    def test_unknown_member_is_404(self, client: TestClient, workspace_id: str) -> None:
        response = client.patch(
            f"/api/workspaces/{workspace_id}/members/mbr_01HZZZZZZZZZZZZZZZZZZZZZZZ",
            json={"role": "viewer"},
            headers=_HEADERS,
        )
        assert response.status_code == 404
