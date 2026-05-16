from __future__ import annotations

import sqlite3

from bootstrap.storage.migrations import (
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
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "_bootstrap_migrations" in tables
    assert "entities" in tables
    assert "objects" in tables
    assert current_version(conn) >= 1
