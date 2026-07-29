"""Session login: signup, login, logout, revocation.

These assert the properties that make session auth *real* rather than
decorative — that logout invalidates the token server-side, that a revoked
session stops working immediately, and that a failed login can't be used to
enumerate accounts.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from selfevals.api.app import build_app
from selfevals.api.credentials import hash_token
from selfevals.api.routes.auth import SESSION_COOKIE
from selfevals.storage.factory import open_storage
from selfevals.storage.postgres import identity as idq

_PASSWORD = "correct horse battery staple"


@pytest.fixture
def client(db_url: str, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("SELFEVALS_AUTH_MODE", "local")
    return TestClient(build_app(db_path=db_url))


def _signup(client: TestClient, email: str = "ada@example.com") -> dict[str, object]:
    response = client.post(
        "/api/auth/signup",
        json={"email": email, "password": _PASSWORD, "display_name": "Ada"},
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


class TestSignup:
    def test_first_signup_creates_a_user_and_signs_them_in(self, client: TestClient) -> None:
        body = _signup(client)
        assert body["email"] == "ada@example.com"
        assert SESSION_COOKIE in client.cookies

        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["id"] == body["id"]

    def test_signup_closes_after_the_first_user(self, client: TestClient) -> None:
        """A self-hosted instance on a public address must not be an open form."""
        _signup(client)
        second = client.post(
            "/api/auth/signup", json={"email": "eve@example.com", "password": _PASSWORD}
        )
        assert second.status_code == 403
        assert "invite" in second.json()["detail"]

    def test_password_is_never_stored_in_clear(self, client: TestClient, db_url: str) -> None:
        _signup(client)
        storage = open_storage(db_url)
        try:
            user = idq.get_user_by_email(storage.identity_connection(), "ada@example.com")
        finally:
            storage.close()
        assert user is not None and user.password_hash
        assert _PASSWORD not in user.password_hash
        assert user.password_hash.startswith("$argon2id$")

    def test_short_passwords_are_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/auth/signup", json={"email": "ada@example.com", "password": "short"}
        )
        assert response.status_code == 422

    def test_malformed_email_is_rejected(self, client: TestClient) -> None:
        """The shapes a hand-rolled regex lets through."""
        response = client.post(
            "/api/auth/signup", json={"email": "a..b@x.com", "password": _PASSWORD}
        )
        assert response.status_code == 422


class TestLogin:
    def test_login_with_correct_password_starts_a_session(self, client: TestClient) -> None:
        _signup(client)
        client.cookies.clear()

        response = client.post(
            "/api/auth/login", json={"email": "ada@example.com", "password": _PASSWORD}
        )
        assert response.status_code == 200
        assert client.get("/api/auth/me").status_code == 200

    def test_email_is_case_insensitive(self, client: TestClient) -> None:
        """`Ada@Example.com` and `ada@example.com` are one account, not two."""
        _signup(client)
        client.cookies.clear()
        response = client.post(
            "/api/auth/login", json={"email": "ADA@EXAMPLE.COM", "password": _PASSWORD}
        )
        assert response.status_code == 200

    def test_wrong_password_is_rejected(self, client: TestClient) -> None:
        _signup(client)
        client.cookies.clear()
        response = client.post(
            "/api/auth/login", json={"email": "ada@example.com", "password": "wrong-password"}
        )
        assert response.status_code == 401

    def test_unknown_and_wrong_password_are_indistinguishable(self, client: TestClient) -> None:
        """Differing responses would turn login into an account enumerator."""
        _signup(client)
        client.cookies.clear()

        unknown = client.post(
            "/api/auth/login", json={"email": "nobody@example.com", "password": _PASSWORD}
        )
        wrong = client.post(
            "/api/auth/login", json={"email": "ada@example.com", "password": "nope-nope-nope"}
        )
        assert unknown.status_code == wrong.status_code == 401
        assert unknown.json()["detail"] == wrong.json()["detail"]


class TestSessionLifecycle:
    def test_logout_revokes_the_session_server_side(
        self, client: TestClient, db_url: str
    ) -> None:
        """Clearing the cookie alone would leave a captured token working."""
        _signup(client)
        token = client.cookies[SESSION_COOKIE]

        assert client.post("/api/auth/logout").status_code == 204

        # Even replaying the exact token fails: the row is revoked.
        client.cookies.set(SESSION_COOKIE, token)
        assert client.get("/api/auth/me").status_code == 401

        storage = open_storage(db_url)
        try:
            assert idq.resolve_session(storage.identity_connection(), hash_token(token)) is None
        finally:
            storage.close()

    def test_logout_is_idempotent_without_a_session(self, client: TestClient) -> None:
        assert client.post("/api/auth/logout").status_code == 204

    def test_me_requires_a_session(self, client: TestClient) -> None:
        assert client.get("/api/auth/me").status_code == 401

    def test_forged_cookie_is_rejected(self, client: TestClient) -> None:
        client.cookies.set(SESSION_COOKIE, "not-a-real-token")
        assert client.get("/api/auth/me").status_code == 401

    def test_logout_everywhere_kills_every_session(
        self, client: TestClient, db_url: str
    ) -> None:
        """The response to a suspected leak: other devices must drop too."""
        _signup(client)
        first = client.cookies[SESSION_COOKIE]

        # A second login = a second device.
        client.cookies.clear()
        client.post("/api/auth/login", json={"email": "ada@example.com", "password": _PASSWORD})
        second = client.cookies[SESSION_COOKIE]
        assert first != second

        assert client.post("/api/auth/logout-everywhere").status_code == 204

        for token in (first, second):
            client.cookies.set(SESSION_COOKIE, token)
            assert client.get("/api/auth/me").status_code == 401

    def test_session_cookie_is_httponly_and_samesite(self, client: TestClient) -> None:
        """OWASP: the token must be unreachable from JavaScript."""
        response = client.post(
            "/api/auth/signup", json={"email": "ada@example.com", "password": _PASSWORD}
        )
        cookie_header = response.headers["set-cookie"].lower()
        assert "httponly" in cookie_header
        assert "samesite=lax" in cookie_header

    def test_secure_flag_follows_the_scheme(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`Secure` over TLS, not over plain HTTP.

        Setting it unconditionally breaks `selfevals serve` locally: the browser
        takes the cookie and then never sends it back, so login appears to work
        and silently doesn't stick.
        """
        monkeypatch.setenv("SELFEVALS_AUTH_MODE", "local")
        app = build_app(db_path=db_url)

        plain = TestClient(app).post(
            "/api/auth/signup", json={"email": "ada@example.com", "password": _PASSWORD}
        )
        assert "secure" not in plain.headers["set-cookie"].lower()

        # A proxy that terminates TLS forwards plain HTTP with this header.
        forwarded = TestClient(app).post(
            "/api/auth/login",
            json={"email": "ada@example.com", "password": _PASSWORD},
            headers={"X-Forwarded-Proto": "https"},
        )
        assert "secure" in forwarded.headers["set-cookie"].lower()
