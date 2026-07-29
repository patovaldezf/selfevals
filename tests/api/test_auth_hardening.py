"""Regression tests for three auth holes closed together.

Each of these was silent: nothing failed, nothing logged, and the deployment
looked authenticated. They share a shape — a default that fails *open* — so they
share a test module.
"""

from __future__ import annotations

import time

import pytest
from fastapi import HTTPException

from selfevals.api.auth import auth_mode, resolve_principal
from selfevals.api.tokens import (
    TokenError,
    _sign_v1,
    issue_token,
    issue_token_with_expiry,
    verify_token,
)


class TestAuthModeIsValidated:
    """A typo in SELFEVALS_AUTH_MODE used to disable authentication.

    Any unrecognized value fell through to the `header` branch — "trust whoever
    the caller claims to be". `SELFEVALS_AUTH_MODE=tokne` therefore turned a
    deployment that believed it required signed tokens into one that accepted
    any identity, with no error anywhere.
    """

    def test_unknown_mode_raises_instead_of_trusting_the_header(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SELFEVALS_AUTH_MODE", "tokne")
        with pytest.raises(HTTPException) as exc:
            resolve_principal("attacker")
        assert exc.value.status_code == 500
        assert "invalid SELFEVALS_AUTH_MODE" in str(exc.value.detail)

    @pytest.mark.parametrize("mode", ["local", "header", "token"])
    def test_known_modes_are_accepted(self, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
        monkeypatch.setenv("SELFEVALS_AUTH_MODE", mode)
        assert auth_mode() == mode

    def test_mode_is_normalized(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Casing and stray whitespace in an env var shouldn't break a deploy."""
        monkeypatch.setenv("SELFEVALS_AUTH_MODE", "  TOKEN ")
        assert auth_mode() == "token"

    def test_unset_defaults_to_local(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SELFEVALS_AUTH_MODE", raising=False)
        assert auth_mode() == "local"


class TestTokenPayloadEncoding:
    """A user id containing a dot produced a token that couldn't be verified.

    v1 inlined the id into a dot-separated string and split on `.` to read it
    back, so `issue_token` happily returned a token that `verify_token` then
    called malformed. That covers essentially every email address — the exact
    identifier a real login would use.
    """

    @pytest.fixture(autouse=True)
    def _secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SELFEVALS_AUTH_SECRET", "test-secret")

    @pytest.mark.parametrize(
        "user_id",
        [
            "pato.valdez@gmail.com",
            "plain",
            "auth0|abc.def",
            "user.with.many.dots",
            "unicode.ñoño@example.com",
        ],
    )
    def test_round_trips_ids_that_v1_could_not(self, user_id: str) -> None:
        assert verify_token(issue_token(user_id)).user_id == user_id

    def test_issue_returns_expiry_so_callers_never_parse_the_token(self) -> None:
        """The session endpoint used to slice the token to recover its expiry."""
        token, expires_at = issue_token_with_expiry("a.b@c.d", ttl_seconds=60)
        assert verify_token(token).expires_at == expires_at
        assert expires_at > int(time.time())

    def test_forged_signature_is_rejected(self) -> None:
        token = issue_token("someone")
        prefix, payload, _ = token.split(".")
        with pytest.raises(TokenError, match="invalid signature"):
            verify_token(f"{prefix}.{payload}.{'0' * 64}")

    def test_tampered_payload_is_rejected(self) -> None:
        """Re-encoding the payload must invalidate the signature."""
        from selfevals.api.tokens import _b64encode

        token = issue_token("victim")
        _, _, signature = token.split(".")
        forged_payload = _b64encode(f"admin|{int(time.time()) + 3600}")
        with pytest.raises(TokenError, match="invalid signature"):
            verify_token(f"v2.{forged_payload}.{signature}")

    def test_expired_token_is_rejected(self) -> None:
        with pytest.raises(TokenError, match="expired"):
            verify_token(issue_token("someone", ttl_seconds=-1))

    def test_garbage_is_rejected(self) -> None:
        with pytest.raises(TokenError):
            verify_token("not-a-token")

    def test_v1_tokens_stay_valid_until_they_expire(self) -> None:
        """Rolling out the new format must not log everyone out mid-session."""
        expires_at = int(time.time()) + 3600
        legacy = f"legacy-user.{expires_at}.{_sign_v1('legacy-user', expires_at)}"
        assert verify_token(legacy).user_id == "legacy-user"

    def test_expired_v1_token_is_rejected(self) -> None:
        expired = int(time.time()) - 1
        legacy = f"old.{expired}.{_sign_v1('old', expired)}"
        with pytest.raises(TokenError, match="expired"):
            verify_token(legacy)


class TestWorkspaceCreationGrantsAdminOnly:
    """Creating a workspace over the API used to grant the creator every role.

    Admin already implies full read+write, so the extra rows bought nothing —
    while silently handing the creator `auditor` too, collapsing the separation
    of duties that role exists to provide.
    """

    def test_creator_gets_admin_not_every_role(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from selfevals.api.app import build_app
        from selfevals.schemas.enums import Role
        from selfevals.storage.factory import open_storage

        monkeypatch.setenv("SELFEVALS_AUTH_MODE", "header")
        client = TestClient(build_app(db_path=db_url))

        created = client.post(
            "/api/workspaces",
            json={"slug": "sep-of-duties", "name": "Sep of duties"},
            headers={"X-SelfEvals-User": "creator"},
        )
        assert created.status_code == 201, created.text
        workspace_id = created.json()["id"]

        storage = open_storage(db_url)
        try:
            roles = storage.workspace_member_roles(
                workspace_id=workspace_id, user_id="creator"
            )
        finally:
            storage.close()

        assert roles == [Role.ADMIN], f"expected admin only, got {roles}"

    def test_creator_can_still_write_to_their_workspace(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Narrowing the grant must not lock the creator out of their own workspace."""
        from fastapi.testclient import TestClient

        from selfevals.api.app import build_app

        monkeypatch.setenv("SELFEVALS_AUTH_MODE", "header")
        client = TestClient(build_app(db_path=db_url))
        headers = {"X-SelfEvals-User": "creator"}

        created = client.post(
            "/api/workspaces", json={"slug": "still-writable"}, headers=headers
        )
        workspace_id = created.json()["id"]

        # A write that fails authorization returns 403; anything else (404/422)
        # means we got past the auth gate, which is what this asserts.
        response = client.post(
            f"/api/workspaces/{workspace_id}/datasets", json={}, headers=headers
        )
        assert response.status_code != 403, response.text
