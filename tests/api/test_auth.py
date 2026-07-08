from __future__ import annotations

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from selfevals.api.app import build_app
from selfevals.api.auth import readable_workspace_ids
from selfevals.api.tokens import issue_token
from selfevals.schemas.enums import Role
from selfevals.schemas.workspace import Workspace
from selfevals.storage.factory import open_storage
from selfevals.storage.seed import seed_workspace


def _client(db_url: str, monkeypatch: MonkeyPatch) -> TestClient:
    monkeypatch.setenv("SELFEVALS_AUTH_MODE", "header")
    return TestClient(build_app(db_path=db_url))


def _token_client(db_url: str, monkeypatch: MonkeyPatch, *, secret: str = "s3cr3t") -> TestClient:
    monkeypatch.setenv("SELFEVALS_AUTH_MODE", "token")
    monkeypatch.setenv("SELFEVALS_AUTH_SECRET", secret)
    return TestClient(build_app(db_path=db_url))


def _seed_user(storage_url: str, *, user_id: str, role: Role) -> Workspace:
    storage = open_storage(storage_url)
    try:
        seeded = seed_workspace(
            storage, slug=f"auth-{user_id}", name="auth", user_id=user_id, assign_all_roles=False
        )
        ws = seeded.workspace
        member = seeded.members[0].model_copy(update={"role": role})
        with storage.open(ws.id) as scope:
            scope.put_entity(member)
        return ws
    finally:
        storage.close()


def test_strict_auth_requires_user_header(db_url: str, monkeypatch: MonkeyPatch) -> None:
    storage = open_storage(db_url)
    try:
        ws = seed_workspace(storage, slug="auth", name="auth", user_id="owner").workspace
    finally:
        storage.close()

    response = _client(db_url, monkeypatch).get(f"/api/workspaces/{ws.id}")
    assert response.status_code == 401


def test_strict_auth_rejects_non_member(db_url: str, monkeypatch: MonkeyPatch) -> None:
    storage = open_storage(db_url)
    try:
        ws = seed_workspace(storage, slug="auth", name="auth", user_id="owner").workspace
    finally:
        storage.close()

    response = _client(db_url, monkeypatch).get(
        f"/api/workspaces/{ws.id}",
        headers={"X-SelfEvals-User": "stranger"},
    )
    assert response.status_code == 403


def test_strict_auth_allows_member_reads(db_url: str, monkeypatch: MonkeyPatch) -> None:
    ws = _seed_user(db_url, user_id="viewer", role=Role.VIEWER)

    response = _client(db_url, monkeypatch).get(
        f"/api/workspaces/{ws.id}",
        headers={"X-SelfEvals-User": "viewer"},
    )
    assert response.status_code == 200


def test_strict_auth_rejects_viewer_mutations(
    db_url: str, monkeypatch: MonkeyPatch
) -> None:
    ws = _seed_user(db_url, user_id="viewer", role=Role.VIEWER)

    response = _client(db_url, monkeypatch).post(
        f"/api/workspaces/{ws.id}/datasets",
        headers={"X-SelfEvals-User": "viewer"},
        json={},
    )
    assert response.status_code == 403


def test_strict_auth_allows_experimenter_mutation_auth(
    db_url: str, monkeypatch: MonkeyPatch
) -> None:
    ws = _seed_user(db_url, user_id="experimenter", role=Role.EXPERIMENTER)

    response = _client(db_url, monkeypatch).post(
        f"/api/workspaces/{ws.id}/datasets",
        headers={"X-SelfEvals-User": "experimenter"},
        json={},
    )
    assert response.status_code == 422


def test_strict_auth_workspaces_index_hides_other_workspaces(
    db_url: str, monkeypatch: MonkeyPatch
) -> None:
    """GET /api/workspaces has no workspace_id in its path, so the
    per-route authorization middleware never runs `authorize_workspace` on
    it — it must filter results itself via `readable_workspace_ids`,
    otherwise a member of one workspace could enumerate every workspace on
    the server."""
    mine = _seed_user(db_url, user_id="viewer", role=Role.VIEWER)
    storage = open_storage(db_url)
    try:
        seed_workspace(storage, slug="other", name="other", user_id="owner")
    finally:
        storage.close()

    response = _client(db_url, monkeypatch).get(
        "/api/workspaces",
        headers={"X-SelfEvals-User": "viewer"},
    )
    assert response.status_code == 200
    ids = {ws["id"] for ws in response.json()["workspaces"]}
    assert ids == {mine.id}


def test_readable_workspace_ids_is_unfiltered_in_local_mode(db_url: str) -> None:
    storage = open_storage(db_url)
    try:
        ws = seed_workspace(storage, slug="local", name="local", user_id="owner").workspace
        assert (
            readable_workspace_ids(storage, candidate_ids=[ws.id, "ws_other"], user=None) is None
        )
    finally:
        storage.close()


def test_readable_workspace_ids_filters_to_member_roles(
    db_url: str, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("SELFEVALS_AUTH_MODE", "header")
    mine = _seed_user(db_url, user_id="viewer", role=Role.VIEWER)
    storage = open_storage(db_url)
    try:
        other = seed_workspace(storage, slug="other", name="other", user_id="owner").workspace
        allowed = readable_workspace_ids(
            storage, candidate_ids=[mine.id, other.id], user="viewer"
        )
        assert allowed == {mine.id}
    finally:
        storage.close()


def test_token_auth_requires_signed_token(db_url: str, monkeypatch: MonkeyPatch) -> None:
    ws = _seed_user(db_url, user_id="viewer", role=Role.VIEWER)

    response = _token_client(db_url, monkeypatch).get(
        f"/api/workspaces/{ws.id}",
        headers={"X-SelfEvals-User": "viewer"},
    )
    assert response.status_code == 401


def test_token_auth_rejects_forged_token(db_url: str, monkeypatch: MonkeyPatch) -> None:
    ws = _seed_user(db_url, user_id="viewer", role=Role.VIEWER)
    monkeypatch.setenv("SELFEVALS_AUTH_SECRET", "s3cr3t")
    forged = issue_token("viewer", ttl_seconds=60).rsplit(".", 1)[0] + ".deadbeef"

    response = _token_client(db_url, monkeypatch).get(
        f"/api/workspaces/{ws.id}",
        headers={"X-SelfEvals-User": forged},
    )
    assert response.status_code == 401


def test_token_auth_accepts_valid_signed_token(db_url: str, monkeypatch: MonkeyPatch) -> None:
    ws = _seed_user(db_url, user_id="viewer", role=Role.VIEWER)
    monkeypatch.setenv("SELFEVALS_AUTH_SECRET", "s3cr3t")
    token = issue_token("viewer", ttl_seconds=60)

    response = _token_client(db_url, monkeypatch).get(
        f"/api/workspaces/{ws.id}",
        headers={"X-SelfEvals-User": token},
    )
    assert response.status_code == 200


def test_token_auth_rejects_expired_token(db_url: str, monkeypatch: MonkeyPatch) -> None:
    ws = _seed_user(db_url, user_id="viewer", role=Role.VIEWER)
    monkeypatch.setenv("SELFEVALS_AUTH_SECRET", "s3cr3t")
    expired = issue_token("viewer", ttl_seconds=-10)

    response = _token_client(db_url, monkeypatch).get(
        f"/api/workspaces/{ws.id}",
        headers={"X-SelfEvals-User": expired},
    )
    assert response.status_code == 401


def test_issue_session_requires_operator_secret(db_url: str, monkeypatch: MonkeyPatch) -> None:
    client = _token_client(db_url, monkeypatch, secret="s3cr3t")

    response = client.post("/api/auth/session", json={"user_id": "alice"})
    assert response.status_code == 401


def test_issue_session_rejects_wrong_operator_secret(
    db_url: str, monkeypatch: MonkeyPatch
) -> None:
    client = _token_client(db_url, monkeypatch, secret="s3cr3t")

    response = client.post(
        "/api/auth/session",
        json={"user_id": "alice"},
        headers={"X-SelfEvals-Operator-Secret": "wrong"},
    )
    assert response.status_code == 401


def test_issue_session_returns_usable_token(db_url: str, monkeypatch: MonkeyPatch) -> None:
    ws = _seed_user(db_url, user_id="alice", role=Role.VIEWER)
    client = _token_client(db_url, monkeypatch, secret="s3cr3t")

    response = client.post(
        "/api/auth/session",
        json={"user_id": "alice"},
        headers={"X-SelfEvals-Operator-Secret": "s3cr3t"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == "alice"

    followup = client.get(
        f"/api/workspaces/{ws.id}",
        headers={"X-SelfEvals-User": body["token"]},
    )
    assert followup.status_code == 200


def test_issue_session_rejected_outside_token_mode(db_url: str, monkeypatch: MonkeyPatch) -> None:
    client = _client(db_url, monkeypatch)

    response = client.post(
        "/api/auth/session",
        json={"user_id": "alice"},
        headers={"X-SelfEvals-Operator-Secret": "anything"},
    )
    assert response.status_code == 400
