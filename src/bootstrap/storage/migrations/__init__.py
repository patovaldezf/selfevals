"""Versioned SQL migrations for SQLiteStorage.

The runner is intentionally minimal: each migration is a Python module
named `mNNNN_<slug>.py` exposing an `up(conn)` function that runs DDL.
Applied versions are tracked in a `_bootstrap_migrations` table.

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


def apply_migrations(conn: sqlite3.Connection) -> list[int]:
    """Apply any pending migrations. Return the list of versions applied."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS _bootstrap_migrations ("
        "  version INTEGER PRIMARY KEY,"
        "  name TEXT NOT NULL,"
        "  applied_at TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    )
    applied = {
        row[0] for row in conn.execute("SELECT version FROM _bootstrap_migrations").fetchall()
    }
    newly_applied: list[int] = []
    for migration in discover_migrations():
        if migration.version in applied:
            continue
        migration.module.up(conn)
        conn.execute(
            "INSERT INTO _bootstrap_migrations (version, name) VALUES (?, ?)",
            (migration.version, migration.name),
        )
        conn.commit()
        newly_applied.append(migration.version)
    return newly_applied


def current_version(conn: sqlite3.Connection) -> int:
    """Return the highest applied migration version, or 0 if none."""
    row = conn.execute("SELECT MAX(version) FROM _bootstrap_migrations").fetchone()
    if row is None or row[0] is None:
        return 0
    value: int = row[0]
    return value
