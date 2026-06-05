from __future__ import annotations

import sqlite3

from selfevals.storage.migrations import (
    apply_migrations,
    current_version,
    discover_migrations,
)


def test_discover_returns_sorted_migrations() -> None:
    ms = discover_migrations()
    assert len(ms) >= 1
    versions = [m.version for m in ms]
    assert versions == sorted(versions)


def test_apply_migrations_creates_tables_and_tracks_version() -> None:
    conn = sqlite3.connect(":memory:")
    applied = apply_migrations(conn)
    assert 1 in applied
    # Re-applying is a no-op.
    again = apply_migrations(conn)
    assert again == []
    # Tables are present.
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "_selfevalss_migrations" in tables
    assert "entities" in tables
    assert "objects" in tables
    assert current_version(conn) >= 1


# A minimal stand-in for the schema a pre-tracking database carries: just
# enough of `entities` to trip the "already exists" path m0001 used to hit.
_LEGACY_ENTITIES_DDL = """
    CREATE TABLE entities (
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
"""


def test_backfill_marks_v1_when_entities_exists_untracked() -> None:
    # Simulate a database created before the migration tracker existed: it has
    # `entities` but no tracking rows. The old code re-ran m0001 here and blew
    # up with "table entities already exists".
    conn = sqlite3.connect(":memory:")
    conn.executescript(_LEGACY_ENTITIES_DDL)

    applied = apply_migrations(conn)  # must not raise
    # v1 was backfilled (recognized as already present), not re-applied.
    assert applied == []
    assert current_version(conn) == 1

    # And it is durably a no-op on the next open.
    assert apply_migrations(conn) == []
    assert current_version(conn) == 1


def test_backfill_from_bootstrap_migrations_legacy() -> None:
    # A database whose migrations were tracked in the legacy
    # `_bootstrap_migrations` table. Its applied versions are authoritative and
    # should be copied into the new tracker rather than re-run.
    conn = sqlite3.connect(":memory:")
    conn.executescript(_LEGACY_ENTITIES_DDL)
    conn.execute("CREATE TABLE _bootstrap_migrations (version INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO _bootstrap_migrations (version) VALUES (1)")
    conn.commit()

    applied = apply_migrations(conn)  # must not re-run m0001
    assert applied == []
    assert current_version(conn) == 1
    # The legacy version was migrated into the canonical tracking table.
    names = {
        row[0]
        for row in conn.execute("SELECT name FROM _selfevalss_migrations").fetchall()
    }
    assert "m0001_initial" in names


def test_backfill_noop_when_tracking_present() -> None:
    # A normally-migrated database must not get duplicate or shifted rows on a
    # subsequent open — the tracker, when populated, is left untouched.
    conn = sqlite3.connect(":memory:")
    apply_migrations(conn)
    before = conn.execute("SELECT version, name FROM _selfevalss_migrations").fetchall()

    apply_migrations(conn)
    after = conn.execute("SELECT version, name FROM _selfevalss_migrations").fetchall()
    assert before == after


def test_idempotent_up_on_existing_schema() -> None:
    # Defense in depth: m0001.up() is safe to run twice on its own (IF NOT
    # EXISTS), independent of the tracker.
    from selfevals.storage.migrations import m0001_initial

    conn = sqlite3.connect(":memory:")
    m0001_initial.up(conn)
    m0001_initial.up(conn)  # must not raise
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "entities" in tables
    assert "objects" in tables
