"""m0001 — foundation + workspace/member tables (relational-canonical).

This is the first slice of the fully-normalized Postgres schema. Every entity
gets its own typed table(s); there is no generic ``entities`` table. Shared
bookkeeping columns (id, workspace_id, version, created_at, updated_at,
deleted_at) live on every main entity table.

Subsequent migrations add the remaining entities (experiments, iterations,
eval_cases, traces + span child tables, decisions, datasets, run_jobs, and the
cold entities) layer by layer. Keeping each layer in its own migration keeps the
forward-only history readable and each deploy reviewable.
"""

from __future__ import annotations

from typing import Any

_SQL = """
-- Object store metadata (blobs themselves live on the filesystem object store).
CREATE TABLE IF NOT EXISTS objects (
    pointer      TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    key          TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    byte_size    INTEGER NOT NULL CHECK (byte_size >= 0),
    created_at   TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_objects_workspace ON objects (workspace_id);
CREATE INDEX IF NOT EXISTS idx_objects_content_hash ON objects (content_hash);

-- Workspace: the tenant boundary. workspace_id == id for a workspace (its own
-- tenant), enforced at the schema layer and mirrored here as a CHECK.
CREATE TABLE IF NOT EXISTS workspaces (
    id           TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    version      INTEGER NOT NULL CHECK (version >= 1),
    created_at   TIMESTAMPTZ NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL,
    deleted_at   TIMESTAMPTZ,
    slug         TEXT NOT NULL,
    name         TEXT NOT NULL,
    description  TEXT,
    owner_id     TEXT,
    -- WorkspaceSettings (small, fixed shape -> typed columns, not JSONB).
    settings_default_runtime TEXT NOT NULL DEFAULT 'offline',
    settings_retention_days  INTEGER NOT NULL DEFAULT 365
        CHECK (settings_retention_days BETWEEN 1 AND 3650),
    CONSTRAINT workspaces_self_tenant CHECK (workspace_id = id),
    CONSTRAINT workspaces_slug_unique UNIQUE (slug)
);
CREATE INDEX IF NOT EXISTS idx_workspaces_owner ON workspaces (owner_id);

-- Member: user-in-workspace assignment with a role.
CREATE TABLE IF NOT EXISTS members (
    id           TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    version      INTEGER NOT NULL CHECK (version >= 1),
    created_at   TIMESTAMPTZ NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL,
    deleted_at   TIMESTAMPTZ,
    user_id      TEXT NOT NULL,
    role         TEXT NOT NULL CHECK (
        role IN ('viewer', 'evaluator', 'experimenter', 'maintainer', 'admin', 'auditor')
    ),
    invited_by   TEXT,
    CONSTRAINT members_user_unique UNIQUE (workspace_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_members_workspace ON members (workspace_id);
"""


def up(cur: Any) -> None:
    cur.execute(_SQL)
