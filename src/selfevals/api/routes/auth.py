"""Signup, login, logout, and the caller's own identity.

Session-based, not JWT: the cookie carries an opaque high-entropy token whose
hash is stored server-side, so logout and "sign out everywhere" are an UPDATE
rather than a wait for expiry. Per OWASP, the token never appears in
`localStorage` and the cookie is `HttpOnly` — a XSS bug cannot read it.

The frontend is same-origin with the API (SvelteKit proxies `/api/*`), so the
cookie needs no cross-site relaxation and `SameSite=Lax` holds.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Any

from fastapi import Cookie, Depends, FastAPI, HTTPException, Request, Response

from selfevals._internal.time import utc_now
from selfevals.api.credentials import (
    hash_password,
    hash_token,
    needs_rehash,
    new_session_token,
    verify_password,
)
from selfevals.api.deps import AppDeps
from selfevals.api.schemas import LoginRequest, SignupRequest, UserResponse
from selfevals.schemas.user import User, UserSession
from selfevals.storage.interface import StorageInterface
from selfevals.storage.postgres import identity as idq

SESSION_COOKIE = "selfevals_session"
_SESSION_TTL = timedelta(days=30)

SessionCookie = Annotated[str | None, Cookie(alias=SESSION_COOKIE)]


def _cookie_is_secure(request: Request) -> bool:
    """Whether to mark the session cookie `Secure`.

    `Secure` means "only send this over HTTPS", so setting it unconditionally
    breaks the plain-HTTP path that `selfevals serve` uses locally: the browser
    accepts the cookie and then never sends it back, and login silently fails to
    stick. Mirroring the request's scheme keeps local development working while
    still marking the cookie `Secure` wherever TLS is in play.

    `X-Forwarded-Proto` is honoured because the SvelteKit proxy and any
    production reverse proxy terminate TLS and forward plain HTTP inward — the
    request object alone would report `http` and lose the flag.
    """
    forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return (forwarded or request.url.scheme) == "https"


def _set_session_cookie(response: Response, token: str, *, secure: bool) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(_SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def _issue_session(conn: Any, *, user: User, request: Request) -> str:
    """Create a session row and return the token to put in the cookie."""
    token = new_session_token()
    session = UserSession(
        user_id=user.id,
        expires_at=utc_now() + _SESSION_TTL,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    idq.create_session(conn, session, token_hash=hash_token(token))
    idq.touch_last_login(conn, user.id)
    return token


def register(app: FastAPI, deps: AppDeps) -> None:
    @app.post("/api/auth/signup", response_model=UserResponse, status_code=201, tags=["auth"])
    def signup(
        body: SignupRequest,
        request: Request,
        response: Response,
        storage: StorageInterface = Depends(deps.storage),
    ) -> UserResponse:
        """Register a user and start a session.

        Open only while the instance has no users: the first signup bootstraps
        the operator of a fresh deployment, and after that accounts are created
        by invitation. Otherwise a self-hosted instance on a public address is
        an open registration form.
        """
        conn = storage.identity_connection()
        if idq.count_users(conn) > 0:
            raise HTTPException(
                status_code=403,
                detail="signup is closed; ask an admin to invite you",
            )
        if idq.get_user_by_email(conn, body.email) is not None:
            raise HTTPException(status_code=409, detail="email already registered")

        user = idq.create_user(
            conn,
            User(
                email=body.email,
                display_name=body.display_name,
                password_hash=hash_password(body.password),
            ),
        )
        _set_session_cookie(
            response,
            _issue_session(conn, user=user, request=request),
            secure=_cookie_is_secure(request),
        )
        return UserResponse(id=user.id, email=user.email, display_name=user.display_name)

    @app.post("/api/auth/login", response_model=UserResponse, tags=["auth"])
    def login(
        body: LoginRequest,
        request: Request,
        response: Response,
        storage: StorageInterface = Depends(deps.storage),
    ) -> UserResponse:
        conn = storage.identity_connection()
        user = idq.get_user_by_email(conn, body.email)

        # One message and one code for "no such user" and "wrong password":
        # distinguishing them turns the endpoint into an account enumerator.
        if user is None or not user.password_hash:
            raise HTTPException(status_code=401, detail="invalid email or password")
        if not verify_password(body.password, user.password_hash):
            raise HTTPException(status_code=401, detail="invalid email or password")

        # A successful login is the only moment the plaintext is in hand, so
        # it's the only chance to upgrade a hash stored under weaker parameters.
        if needs_rehash(user.password_hash):
            idq.update_password_hash(
                conn, user_id=user.id, password_hash=hash_password(body.password)
            )

        _set_session_cookie(
            response,
            _issue_session(conn, user=user, request=request),
            secure=_cookie_is_secure(request),
        )
        return UserResponse(id=user.id, email=user.email, display_name=user.display_name)

    @app.post("/api/auth/logout", status_code=204, tags=["auth"])
    def logout(
        response: Response,
        session: SessionCookie = None,
        storage: StorageInterface = Depends(deps.storage),
    ) -> None:
        """Revoke the current session. Idempotent — always 204.

        Revoking server-side is what makes this real: clearing the cookie alone
        would leave a token that still works if it was ever captured.
        """
        if session:
            idq.revoke_session(storage.identity_connection(), hash_token(session))
        response.delete_cookie(SESSION_COOKIE, path="/")

    @app.get("/api/auth/me", response_model=UserResponse, tags=["auth"])
    def me(
        session: SessionCookie = None,
        storage: StorageInterface = Depends(deps.storage),
    ) -> UserResponse:
        """The signed-in user. 401 when the session is missing, revoked, or expired."""
        if not session:
            raise HTTPException(status_code=401, detail="not authenticated")
        resolved = idq.resolve_session(storage.identity_connection(), hash_token(session))
        if resolved is None:
            raise HTTPException(status_code=401, detail="session expired or revoked")
        _, user = resolved
        return UserResponse(id=user.id, email=user.email, display_name=user.display_name)

    @app.post("/api/auth/logout-everywhere", status_code=204, tags=["auth"])
    def logout_everywhere(
        response: Response,
        session: SessionCookie = None,
        storage: StorageInterface = Depends(deps.storage),
    ) -> None:
        """Revoke every session for the caller — the response to a suspected leak."""
        if not session:
            raise HTTPException(status_code=401, detail="not authenticated")
        conn = storage.identity_connection()
        resolved = idq.resolve_session(conn, hash_token(session))
        if resolved is None:
            raise HTTPException(status_code=401, detail="session expired or revoked")
        _, user = resolved
        idq.revoke_all_sessions(conn, user.id)
        response.delete_cookie(SESSION_COOKIE, path="/")
