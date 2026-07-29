"""Queries for users, sessions, and API keys.

Kept apart from `queries.py` because identity is the one thing that is *not*
workspace-scoped: a user exists across tenants, so these functions take a raw
connection rather than going through `WorkspaceScope`. Authorization still runs
through `members` exactly as before — this module answers "who is calling?",
never "may they?".
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from selfevals._internal.time import utc_now
from selfevals.schemas.enums import Role
from selfevals.schemas.user import User, UserSession

# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

_USER_COLUMNS = (
    "id, version, created_at, updated_at, deleted_at, "
    "email, display_name, password_hash, last_login_at"
)


def _user_from_row(row: tuple[Any, ...]) -> User:
    return User(
        id=row[0],
        version=row[1],
        created_at=row[2],
        updated_at=row[3],
        deleted_at=row[4],
        email=row[5],
        display_name=row[6],
        password_hash=row[7],
        last_login_at=row[8],
    )


def create_user(conn: Any, user: User) -> User:
    """Insert a user. Raises on duplicate email (unique index on lower(email))."""
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO users ({_USER_COLUMNS}) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                user.id,
                user.version,
                user.created_at,
                user.updated_at,
                user.deleted_at,
                user.email,
                user.display_name,
                user.password_hash,
                user.last_login_at,
            ),
        )
    return user


def get_user(conn: Any, user_id: str) -> User | None:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {_USER_COLUMNS} FROM users WHERE id = %s AND deleted_at IS NULL",
            (user_id,),
        )
        row = cur.fetchone()
    return _user_from_row(row) if row else None


def get_user_by_email(conn: Any, email: str) -> User | None:
    """Look a user up case-insensitively, matching the unique index."""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {_USER_COLUMNS} FROM users "
            "WHERE lower(email) = lower(%s) AND deleted_at IS NULL",
            (email.strip(),),
        )
        row = cur.fetchone()
    return _user_from_row(row) if row else None


def count_users(conn: Any) -> int:
    """Live user count — lets the first signup bootstrap itself as owner."""
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM users WHERE deleted_at IS NULL")
        row = cur.fetchone()
    return int(row[0]) if row else 0


def update_password_hash(conn: Any, *, user_id: str, password_hash: str) -> None:
    """Replace a stored hash — used on password change and on rehash-on-login."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE users SET password_hash = %s, updated_at = %s, version = version + 1 "
            "WHERE id = %s",
            (password_hash, utc_now(), user_id),
        )


def touch_last_login(conn: Any, user_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE users SET last_login_at = %s WHERE id = %s", (utc_now(), user_id))


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def create_session(conn: Any, session: UserSession, *, token_hash: str) -> UserSession:
    """Persist a session. Only the token's hash is stored, never the token."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO user_sessions "
            "(id, user_id, token_hash, created_at, expires_at, revoked_at, user_agent, ip_address) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                session.id,
                session.user_id,
                token_hash,
                session.created_at,
                session.expires_at,
                session.revoked_at,
                session.user_agent,
                session.ip_address,
            ),
        )
    return session


def resolve_session(conn: Any, token_hash: str) -> tuple[UserSession, User] | None:
    """Return the live session and its user, or None.

    One joined query on the request hot path. "Live" is enforced in SQL — an
    expired or revoked session simply does not match, so no caller can forget to
    check.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.id, s.user_id, s.created_at, s.expires_at, s.revoked_at,
                   s.user_agent, s.ip_address,
                   u.id, u.version, u.created_at, u.updated_at, u.deleted_at,
                   u.email, u.display_name, u.password_hash, u.last_login_at
            FROM user_sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = %s
              AND s.revoked_at IS NULL
              AND s.expires_at > %s
              AND u.deleted_at IS NULL
            """,
            (token_hash, utc_now()),
        )
        row = cur.fetchone()
    if row is None:
        return None
    session = UserSession(
        id=row[0],
        user_id=row[1],
        created_at=row[2],
        expires_at=row[3],
        revoked_at=row[4],
        user_agent=row[5],
        ip_address=row[6],
    )
    return session, _user_from_row(tuple(row[7:]))


def revoke_session(conn: Any, token_hash: str) -> bool:
    """Revoke one session (logout). True if a live session was revoked."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE user_sessions SET revoked_at = %s "
            "WHERE token_hash = %s AND revoked_at IS NULL",
            (utc_now(), token_hash),
        )
        return bool(cur.rowcount)


def revoke_all_sessions(conn: Any, user_id: str) -> int:
    """Revoke every live session for a user ("sign out everywhere").

    Also the correct response to a password change or a compromised account.
    """
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE user_sessions SET revoked_at = %s "
            "WHERE user_id = %s AND revoked_at IS NULL",
            (utc_now(), user_id),
        )
        return int(cur.rowcount)


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------


def create_api_key(
    conn: Any,
    *,
    key_id: str,
    workspace_id: str,
    created_by: str,
    name: str,
    prefix: str,
    key_hash: str,
    role: Role,
    expires_at: datetime | None = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO api_keys "
            "(id, workspace_id, created_by, name, prefix, key_hash, role, created_at, expires_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                key_id,
                workspace_id,
                created_by,
                name,
                prefix,
                key_hash,
                role.value,
                utc_now(),
                expires_at,
            ),
        )


def resolve_api_key(conn: Any, key_hash: str) -> tuple[str, str, Role] | None:
    """Return ``(key_id, workspace_id, role)`` for a live key, or None.

    An API key is bound to one workspace: unlike a session, it does not inherit
    everything its creator can reach. That containment is the point — a leaked
    CI key exposes one workspace, not an account.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, workspace_id, role FROM api_keys
            WHERE key_hash = %s
              AND revoked_at IS NULL
              AND (expires_at IS NULL OR expires_at > %s)
            """,
            (key_hash, utc_now()),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return str(row[0]), str(row[1]), Role(str(row[2]))


def touch_api_key(conn: Any, key_id: str) -> None:
    """Record usage. Best-effort: never let telemetry fail a request."""
    with conn.cursor() as cur:
        cur.execute("UPDATE api_keys SET last_used_at = %s WHERE id = %s", (utc_now(), key_id))


def list_api_keys(conn: Any, workspace_id: str) -> list[dict[str, Any]]:
    """List a workspace's live keys — prefixes only, never the secrets."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, name, prefix, role, created_by, created_at, expires_at, last_used_at
            FROM api_keys
            WHERE workspace_id = %s AND revoked_at IS NULL
            ORDER BY created_at DESC
            """,
            (workspace_id,),
        )
        rows = cur.fetchall()
    return [
        {
            "id": r[0],
            "name": r[1],
            "prefix": r[2],
            "role": Role(str(r[3])),
            "created_by": r[4],
            "created_at": r[5],
            "expires_at": r[6],
            "last_used_at": r[7],
        }
        for r in rows
    ]


def revoke_api_key(conn: Any, *, workspace_id: str, key_id: str) -> bool:
    """Revoke a key. Scoped by workspace so one tenant cannot revoke another's."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE api_keys SET revoked_at = %s "
            "WHERE id = %s AND workspace_id = %s AND revoked_at IS NULL",
            (utc_now(), key_id, workspace_id),
        )
        return bool(cur.rowcount)
