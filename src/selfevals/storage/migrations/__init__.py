"""Versioned SQL migrations for SQLiteStorage.

The runner is intentionally minimal: each migration is a Python module
named `mNNNN_<slug>.py` exposing an `up(conn)` function that runs DDL.
Applied versions are tracked in a `_selfevalss_migrations` table.

We do not support downgrades. Forward-only.
"""

from __future__ import annotations

import importlib
import pkgutil
import re
import sqlite3
from dataclasses import dataclass
from typing import Protocol

_MIGRATION_RE = re.compile(r"^m(\d{4})_[a-z0-9_]+$")


class _MigrationModule(Protocol):
    def up(self, conn: sqlite3.Connection) -> None: ...


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    module: _MigrationModule


def discover_migrations() -> list[Migration]:
    """Return all migrations in this package, sorted by version."""
    migrations: list[Migration] = []
    package = importlib.import_module(__name__)
    for info in pkgutil.iter_modules(package.__path__):
        match = _MIGRATION_RE.match(info.name)
        if match is None:
            continue
        version = int(match.group(1))
        module = importlib.import_module(f"{__name__}.{info.name}")
        migrations.append(Migration(version=version, name=info.name, module=module))
    migrations.sort(key=lambda m: m.version)
    return migrations


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _backfill_legacy_tracking(conn: sqlite3.Connection) -> None:
    """Seed the tracking row a pre-tracking database is missing.

    Forward-only migrations assume an empty database starts at version 0 and
    climbs. But a database created by a version that predates the tracker (or
    that tracked migrations in a legacy ``_bootstrap_migrations`` table) already
    has the base schema while ``_selfevalss_migrations`` sits empty. Without
    this, ``apply_migrations`` would re-run m0001 and hit
    ``OperationalError: table entities already exists``.

    The tracker is the source of truth, so only act when it is empty:

    * Legacy ``_bootstrap_migrations`` present → copy its applied versions over
      (it is authoritative about what ran, including any m0002+).
    * Otherwise, ``entities`` present → the base schema (v1) exists, so mark
      *only* v1. Higher migrations may add tables this old database lacks; the
      normal loop must still run them, so we must not mark them applied here.
    """
    has_rows = conn.execute("SELECT 1 FROM _selfevalss_migrations LIMIT 1").fetchone()
    if has_rows is not None:
        return

    # Map known versions to their canonical migration name so a backfilled row
    # is indistinguishable from one the normal loop would have inserted. Derive
    # it from discovery rather than hardcoding, so a slug rename survives.
    names_by_version = {m.version: m.name for m in discover_migrations()}

    def _record(version: int) -> None:
        name = names_by_version.get(version, f"m{version:04d}_legacy")
        conn.execute(
            "INSERT OR IGNORE INTO _selfevalss_migrations (version, name) VALUES (?, ?)",
            (version, name),
        )

    if _table_exists(conn, "_bootstrap_migrations"):
        try:
            legacy_versions = [
                int(row[0])
                for row in conn.execute(
                    "SELECT version FROM _bootstrap_migrations"
                ).fetchall()
            ]
        except sqlite3.OperationalError:
            # Unknown legacy shape (no `version` column) — fall back to the
            # base-schema heuristic below rather than guess at its columns.
            legacy_versions = []
        if legacy_versions:
            for version in legacy_versions:
                _record(version)
            conn.commit()
            return

    if _table_exists(conn, "entities"):
        _record(1)
        conn.commit()


def apply_migrations(conn: sqlite3.Connection) -> list[int]:
    """Apply any pending migrations. Return the list of versions applied."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS _selfevalss_migrations ("
        "  version INTEGER PRIMARY KEY,"
        "  name TEXT NOT NULL,"
        "  applied_at TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    )
    _backfill_legacy_tracking(conn)
    applied = {
        row[0] for row in conn.execute("SELECT version FROM _selfevalss_migrations").fetchall()
    }
    newly_applied: list[int] = []
    for migration in discover_migrations():
        if migration.version in applied:
            continue
        migration.module.up(conn)
        conn.execute(
            "INSERT INTO _selfevalss_migrations (version, name) VALUES (?, ?)",
            (migration.version, migration.name),
        )
        conn.commit()
        newly_applied.append(migration.version)
    return newly_applied


def current_version(conn: sqlite3.Connection) -> int:
    """Return the highest applied migration version, or 0 if none."""
    row = conn.execute("SELECT MAX(version) FROM _selfevalss_migrations").fetchone()
    if row is None or row[0] is None:
        return 0
    value: int = row[0]
    return value
