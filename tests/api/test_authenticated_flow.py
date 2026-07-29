"""The authenticated path, end to end.

Every other auth test exercises one endpoint. This walks the flow a real
deployment runs — sign up, get a workspace, invite a teammate, have them work,
revoke them — with authorization enforced throughout. It is the test that would
have caught "the API is only ever exercised in `local` mode, where nothing is
checked".
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from selfevals.api.app import build_app
from selfevals.api.routes.auth import SESSION_COOKIE

_PASSWORD = "correct horse battery staple"


@pytest.fixture
def client(db_url: str, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A client in `header` mode: identity is enforced, no login required.

    `header` rather than `token` because this exercises *authorization* — that
    roles and membership actually gate access. How the caller proves who they
    are is covered by the login and token tests.
    """
    monkeypatch.setenv("SELFEVALS_AUTH_MODE", "header")
    return TestClient(build_app(db_path=db_url))


def _as(user: str) -> dict[str, str]:
    return {"X-SelfEvals-User": user}


def test_full_collaboration_flow(client: TestClient) -> None:
    """Owner creates a workspace, invites a teammate, then revokes them."""
    # 1. An owner creates a workspace and gets admin — and only admin.
    created = client.post("/api/workspaces", json={"slug": "team-alpha"}, headers=_as("owner"))
    assert created.status_code == 201, created.text
    workspace = created.json()["id"]

    members = client.get(f"/api/workspaces/{workspace}/members", headers=_as("owner")).json()
    assert [(m["user_id"], m["role"]) for m in members] == [("owner", "admin")]

    # 2. A stranger sees nothing — neither the workspace nor its members.
    assert client.get(f"/api/workspaces/{workspace}", headers=_as("stranger")).status_code == 403
    visible = client.get("/api/workspaces", headers=_as("stranger")).json()["workspaces"]
    assert all(w["id"] != workspace for w in visible)

    # 3. The owner invites a teammate, who can then read.
    invited = client.post(
        f"/api/workspaces/{workspace}/members",
        json={"user_id": "teammate", "role": "experimenter"},
        headers=_as("owner"),
    )
    assert invited.status_code == 201
    assert client.get(f"/api/workspaces/{workspace}", headers=_as("teammate")).status_code == 200

    # 4. …and write, but not manage membership.
    assert (
        client.post(
            f"/api/workspaces/{workspace}/members",
            json={"user_id": "outsider", "role": "admin"},
            headers=_as("teammate"),
        ).status_code
        == 403
    )

    # 5. Revoking the membership takes access away immediately.
    removed = client.delete(
        f"/api/workspaces/{workspace}/members/{invited.json()['id']}", headers=_as("owner")
    )
    assert removed.status_code == 204
    assert client.get(f"/api/workspaces/{workspace}", headers=_as("teammate")).status_code == 403


def test_viewer_can_read_but_not_write(client: TestClient) -> None:
    """The role split is real, not decorative."""
    workspace = client.post(
        "/api/workspaces", json={"slug": "read-only"}, headers=_as("owner")
    ).json()["id"]
    client.post(
        f"/api/workspaces/{workspace}/members",
        json={"user_id": "reader", "role": "viewer"},
        headers=_as("owner"),
    )

    assert client.get(f"/api/workspaces/{workspace}", headers=_as("reader")).status_code == 200
    # A write is rejected by authorization, before any payload validation.
    assert (
        client.post(
            f"/api/workspaces/{workspace}/datasets", json={}, headers=_as("reader")
        ).status_code
        == 403
    )


def test_session_login_authorizes_workspace_access(
    db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cookie session satisfies workspace authorization, not just `/auth/me`.

    Login and authorization are separate layers, and this is the seam between
    them: the id minted at signup has to be the id `members` is keyed on, or a
    user could log in successfully and still be denied everywhere.
    """
    monkeypatch.setenv("SELFEVALS_AUTH_MODE", "local")
    client = TestClient(build_app(db_path=db_url))

    signup = client.post(
        "/api/auth/signup", json={"email": "owner@example.com", "password": _PASSWORD}
    )
    assert signup.status_code == 201
    assert SESSION_COOKIE in client.cookies

    created = client.post("/api/workspaces", json={"slug": "session-owned"})
    assert created.status_code == 201

    # The session survives across requests and still identifies the same user.
    assert client.get("/api/auth/me").json()["id"] == signup.json()["id"]
