"""Tests for the Postgres forward-only migration runner."""

from __future__ import annotations

from typing import TYPE_CHECKING

from selfevals.storage.postgres.migrations import (
    apply_migrations,
    current_version,
    discover_migrations,
)

if TYPE_CHECKING:
    from selfevals.storage.interface import StorageInterface


def test_discover_returns_sorted_migrations() -> None:
    ms = discover_migrations()
    assert len(ms) >= 1
    versions = [m.version for m in ms]
    assert versions == sorted(versions)
    # No duplicate version numbers.
    assert len(versions) == len(set(versions))


def test_storage_init_applies_all_migrations(storage: StorageInterface) -> None:
    # The `storage` fixture builds a PostgresStorage, whose __init__ runs the
    # migrations. The version must equal the highest discovered migration.
    conn = storage._conn  # type: ignore[attr-defined]
    highest = max(m.version for m in discover_migrations())
    assert current_version(conn) == highest


def test_apply_migrations_is_idempotent(storage: StorageInterface) -> None:
    conn = storage._conn  # type: ignore[attr-defined]
    # Already fully migrated by __init__; re-applying must be a no-op.
    again = apply_migrations(conn)
    assert again == []


def test_expected_tables_exist(storage: StorageInterface) -> None:
    conn = storage._conn  # type: ignore[attr-defined]
    with conn.cursor() as cur:
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()")
        tables = {row[0] for row in cur.fetchall()}
    # Tracking table plus a representative sample of the normalized schema.
    for expected in {
        "_pg_migrations",
        "workspaces",
        "members",
        "experiments",
        "eval_cases",
        "iteration_records",
        "decision_records",
        "run_jobs",
        "datasets",
        "traces",
        "trace_spans",
        "trace_llm_calls",
        "trace_grader_results",
        "agents",
        "tools",
        "grader_cards",
        "failure_modes",
        "annotations",
    }:
        assert expected in tables, f"missing table {expected!r}"
    # The generic catch-all table from the SQLite era must be gone.
    assert "entities" not in tables
