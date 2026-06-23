"""Forward-only SQL migrations for PostgresStorage.

The runner mirrors the (now retired) SQLite runner: each migration is a Python
module named ``mNNNN_<slug>.py`` exposing an ``up(cur)`` function that runs DDL
against an open psycopg cursor. Applied versions are tracked in a
``_pg_migrations`` table. Forward-only — no downgrades.

Each migration runs inside its own transaction so a failure leaves the schema
at the last good version rather than half-applied.
"""

from __future__ import annotations

import importlib
import pkgutil
import re
from dataclasses import dataclass
from typing import Any, Protocol

_MIGRATION_RE = re.compile(r"^m(\d{4})_[a-z0-9_]+$")


class _MigrationModule(Protocol):
    def up(self, cur: Any) -> None: ...


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


def apply_migrations(conn: Any) -> list[int]:
    """Apply any pending migrations. Return the list of versions applied.

    ``conn`` is a psycopg connection. The tracking table and each migration run
    in their own transactions; ``conn.autocommit`` is honored if already set,
    otherwise we commit explicitly after each step.
    """
    autocommit = bool(getattr(conn, "autocommit", False))
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS _pg_migrations (
                version    INTEGER PRIMARY KEY,
                name       TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    if not autocommit:
        conn.commit()

    with conn.cursor() as cur:
        cur.execute("SELECT version FROM _pg_migrations")
        applied = {int(row[0]) for row in cur.fetchall()}

    newly_applied: list[int] = []
    for migration in discover_migrations():
        if migration.version in applied:
            continue
        with conn.cursor() as cur:
            migration.module.up(cur)
            cur.execute(
                "INSERT INTO _pg_migrations (version, name) VALUES (%s, %s)",
                (migration.version, migration.name),
            )
        if not autocommit:
            conn.commit()
        newly_applied.append(migration.version)
    return newly_applied


def current_version(conn: Any) -> int:
    """Return the highest applied migration version, or 0 if none."""
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(version) FROM _pg_migrations")
        row = cur.fetchone()
    if row is None or row[0] is None:
        return 0
    return int(row[0])
