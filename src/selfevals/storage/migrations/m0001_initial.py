"""Initial schema.

We use a single generic `entities` table for MVP: every Pydantic entity
serializes to a row with (entity_type, id, workspace_id, version, created_at,
updated_at, deleted_at, payload JSON). This trades query-time indexability
for schema agility — adding a new entity type does not need DDL.

Common columns are indexed; payload is opaque text JSON. When a query path
becomes hot we can promote a field to a real column in a later migration
without breaking existing data.

All DDL uses ``IF NOT EXISTS`` so ``up()`` is idempotent on its own — a
second-class defense behind the migration tracker (which already skips
applied versions). This matters for databases predating the tracker, where
``entities`` may exist before any row lands in ``_selfevalss_migrations``;
``apply_migrations`` backfills the tracking row, and this keeps the DDL safe
even if that backfill ever misses a case.
"""

from __future__ import annotations

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS entities (
            entity_type   TEXT    NOT NULL,
            id            TEXT    NOT NULL,
            workspace_id  TEXT    NOT NULL,
            version       INTEGER NOT NULL,
            created_at    TEXT    NOT NULL,
            updated_at    TEXT    NOT NULL,
            deleted_at    TEXT,
            payload       TEXT    NOT NULL,
            PRIMARY KEY (entity_type, id)
        );

        CREATE INDEX IF NOT EXISTS idx_entities_workspace_type
            ON entities (workspace_id, entity_type);

        CREATE INDEX IF NOT EXISTS idx_entities_workspace_type_created
            ON entities (workspace_id, entity_type, created_at DESC);

        CREATE INDEX IF NOT EXISTS idx_entities_workspace_type_updated
            ON entities (workspace_id, entity_type, updated_at DESC);

        CREATE INDEX IF NOT EXISTS idx_entities_deleted_at
            ON entities (deleted_at);

        CREATE TABLE IF NOT EXISTS objects (
            pointer       TEXT    PRIMARY KEY,
            workspace_id  TEXT    NOT NULL,
            key           TEXT    NOT NULL,
            content_hash  TEXT    NOT NULL,
            byte_size     INTEGER NOT NULL,
            created_at    TEXT    NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_objects_workspace ON objects (workspace_id);
        CREATE INDEX IF NOT EXISTS idx_objects_content_hash ON objects (content_hash);
        """
    )
