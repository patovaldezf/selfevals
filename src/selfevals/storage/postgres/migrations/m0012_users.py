"""m0012 — real identity: `users`, `user_sessions`, `api_keys`.

Until now a "user" was an unbacked string: `members.user_id` was free text with
no row behind it, so there was nothing to attach an email, a credential, or a
login to. Two tables close that.

`users` is deliberately NOT wired as a foreign key from `members.user_id` yet.
Every existing deployment has member rows whose user_id is an arbitrary string
("local", a header value, an operator-chosen id); adding the FK now would
require backfilling all of them before the migration could apply. The table
lands alongside them, new writes go through it, and the constraint can be added
later once the data is uniform. A migration that can't run is worse than one
that arrives in two steps.

`user_sessions` exists so logout can mean something. HMAC tokens are stateless,
so without a server-side record the only way to revoke one is rotating the
signing secret — which logs out everyone, everywhere. A session row per issued
token makes single-session revocation (logout) and "sign out everywhere"
ordinary operations, at the cost of one indexed lookup per authenticated
request. Per OWASP, the cookie carries a high-entropy opaque token and we store
only its **hash**: a leaked database backup then yields no usable sessions.

`api_keys` is a separate lane on purpose. For an evals framework most traffic is
SDKs, CI, and adapters — not browsers — so machine credentials are a first-class
concern rather than a corner of the login flow. They are workspace-scoped (a key
grants access to one workspace, not to everything its creator can reach), stored
hashed, and shown once at creation.
"""

from __future__ import annotations

from typing import Any

_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    version       INTEGER NOT NULL CHECK (version >= 1),
    created_at    TIMESTAMPTZ NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL,
    deleted_at    TIMESTAMPTZ,
    -- Case-insensitive uniqueness is enforced by the index below, not here:
    -- nobody should own both Ada@example.com and ada@example.com.
    email         TEXT NOT NULL,
    display_name  TEXT,
    -- NULL means "no password login" (SSO-only, or an API-only identity).
    password_hash TEXT,
    last_login_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_lower
    ON users (lower(email))
    WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS user_sessions (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    -- SHA-256 of the opaque cookie token. The plaintext never lands in the
    -- database, so a dump (or a leaked backup) hands over no live sessions.
    -- Plain SHA-256 is right here, unlike for passwords: the token is 256 bits
    -- of CSPRNG output, so there is nothing to brute-force.
    token_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    -- Provenance for the session list ("you're signed in on 3 devices") and
    -- for spotting a stolen token. Best-effort: proxies may not forward them.
    user_agent TEXT,
    ip_address TEXT
);

-- Every authenticated request looks a session up by token hash, so this index
-- is the hot path.
CREATE UNIQUE INDEX IF NOT EXISTS idx_user_sessions_token
    ON user_sessions (token_hash);
CREATE INDEX IF NOT EXISTS idx_user_sessions_user
    ON user_sessions (user_id)
    WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS api_keys (
    id           TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    -- Who minted it. Text rather than a FK for the same reason as
    -- members.user_id: existing deployments hold arbitrary identity strings.
    created_by   TEXT NOT NULL,
    name         TEXT NOT NULL,
    -- Leading characters of the key, kept in clear so the UI can show
    -- "sk_se_7f3a…" in a list without storing anything usable.
    prefix       TEXT NOT NULL,
    key_hash     TEXT NOT NULL,
    role         TEXT NOT NULL CHECK (
        role IN ('viewer', 'evaluator', 'experimenter', 'maintainer', 'admin', 'auditor')
    ),
    created_at   TIMESTAMPTZ NOT NULL,
    expires_at   TIMESTAMPTZ,
    revoked_at   TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys (key_hash);
CREATE INDEX IF NOT EXISTS idx_api_keys_workspace
    ON api_keys (workspace_id)
    WHERE revoked_at IS NULL;
"""


def up(cur: Any) -> None:
    cur.execute(_SQL)
