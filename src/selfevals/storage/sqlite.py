"""SQLite implementation of StorageInterface + WorkspaceScope.

Every entity is stored in a single generic `entities` table keyed by
(entity_type, id). Workspace isolation is enforced by always filtering on
`workspace_id` — there is no method on `_SQLiteScope` that lets you query
without one.

Optimistic concurrency: writes that change an entity check the stored
`version` against the caller's; mismatches raise OptimisticConcurrencyError.
The caller is expected to bump `version` and refresh `updated_at` before
calling `put_entity`.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

from selfevals._internal.time import utc_now
from selfevals.storage.errors import (
    EntityNotFoundError,
    OptimisticConcurrencyError,
    WorkspaceMismatchError,
)
from selfevals.storage.interface import ListFilter, StorageInterface, WorkspaceScope
from selfevals.storage.migrations import apply_migrations

if TYPE_CHECKING:
    from selfevals.schemas._base import BaseEntity


def _entity_type_name(cls: type[BaseEntity]) -> str:
    """Stable type tag used in the `entity_type` column.

    Uses the class name (e.g. 'Workspace', 'EvalCase'). Subclasses must have
    unique names; we don't namespace by module because storage rehydrates by
    class reference, not by import path.
    """
    return cls.__name__


_ORDERABLE_COLUMNS = {
    "entity_type",
    "id",
    "workspace_id",
    "version",
    "created_at",
    "updated_at",
    "deleted_at",
}


class SQLiteStorage(StorageInterface):
    """SQLite-backed storage. `:memory:` and on-disk paths both supported."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._db_path = str(db_path)
        self._conn = sqlite3.connect(self._db_path, isolation_level=None)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        apply_migrations(self._conn)

    @property
    def connection(self) -> sqlite3.Connection:
        """Underlying connection. Intended for tests and migrations only."""
        return self._conn

    def open(self, workspace_id: str) -> WorkspaceScope:
        if not workspace_id:
            raise ValueError("workspace_id must be a non-empty string")
        return _SQLiteScope(self._conn, workspace_id)

    def close(self) -> None:
        self._conn.close()


class _SQLiteScope(WorkspaceScope):
    def __init__(self, conn: sqlite3.Connection, workspace_id: str) -> None:
        self._conn = conn
        self.workspace_id = workspace_id
        self._closed = False

    def close(self) -> None:
        # The scope does not own the connection — only marks itself closed.
        self._closed = True

    def __enter__(self) -> Self:
        if self._closed:
            raise RuntimeError("scope has been closed")
        return self

    def put_entity(self, entity: BaseEntity) -> None:
        self._guard_open()
        self.assert_owns(entity)
        entity_type = _entity_type_name(type(entity))
        payload = entity.model_dump(mode="json")
        existing = self._conn.execute(
            "SELECT version FROM entities WHERE entity_type = ? AND id = ?",
            (entity_type, entity.id),
        ).fetchone()
        now_iso = utc_now().isoformat()
        if existing is None:
            self._conn.execute(
                "INSERT INTO entities "
                "(entity_type, id, workspace_id, version, created_at, updated_at, deleted_at, payload) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entity_type,
                    entity.id,
                    entity.workspace_id,
                    entity.version,
                    entity.created_at.isoformat(),
                    entity.updated_at.isoformat(),
                    entity.deleted_at.isoformat() if entity.deleted_at else None,
                    json.dumps(payload),
                ),
            )
            return
        stored_version: int = existing[0]
        if stored_version != entity.version - 1 and stored_version != entity.version:
            # We allow same-version writes (re-saving an idempotent state) and
            # forward-by-one bumps. Any other gap is a concurrency violation.
            raise OptimisticConcurrencyError(
                entity_type=entity_type,
                entity_id=entity.id,
                expected=entity.version,
                found=stored_version,
            )
        self._conn.execute(
            "UPDATE entities SET "
            "  workspace_id = ?, version = ?, updated_at = ?, deleted_at = ?, payload = ? "
            "WHERE entity_type = ? AND id = ?",
            (
                entity.workspace_id,
                entity.version,
                now_iso,
                entity.deleted_at.isoformat() if entity.deleted_at else None,
                json.dumps(payload),
                entity_type,
                entity.id,
            ),
        )

    def get_entity(self, entity_type: type[BaseEntity], entity_id: str) -> BaseEntity:
        self._guard_open()
        type_tag = _entity_type_name(entity_type)
        row = self._conn.execute(
            "SELECT workspace_id, payload FROM entities "
            "WHERE entity_type = ? AND id = ? AND workspace_id = ?",
            (type_tag, entity_id, self.workspace_id),
        ).fetchone()
        if row is None:
            # Differentiate "wrong workspace" from "missing" for clearer audit.
            cross = self._conn.execute(
                "SELECT workspace_id FROM entities WHERE entity_type = ? AND id = ?",
                (type_tag, entity_id),
            ).fetchone()
            if cross is not None:
                raise WorkspaceMismatchError(self.workspace_id, cross[0])
            raise EntityNotFoundError(type_tag, entity_id, self.workspace_id)
        payload = json.loads(row[1])
        return entity_type.model_validate(payload)

    def list_entities(
        self,
        entity_type: type[BaseEntity],
        filter_: ListFilter | None = None,
    ) -> list[BaseEntity]:
        self._guard_open()
        type_tag = _entity_type_name(entity_type)
        filter_ = filter_ or ListFilter()
        if filter_.order_by not in _ORDERABLE_COLUMNS:
            raise ValueError(f"unsupported order_by column: {filter_.order_by!r}")
        clauses = ["workspace_id = ?", "entity_type = ?"]
        params: list[Any] = [self.workspace_id, type_tag]
        for k, v in filter_.where.items():
            if k in {
                "workspace_id",
                "entity_type",
                "id",
                "version",
                "created_at",
                "updated_at",
                "deleted_at",
            }:
                clauses.append(f"{k} = ?")
                params.append(v)
            else:
                clauses.append("json_extract(payload, ?) = ?")
                params.extend([f"$.{k}", v])
        sql = (
            "SELECT payload FROM entities "
            f"WHERE {' AND '.join(clauses)} "
            f"ORDER BY {filter_.order_by} {'DESC' if filter_.order_desc else 'ASC'}"
        )
        if filter_.limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([filter_.limit, filter_.offset])
        rows = self._conn.execute(sql, params).fetchall()
        return [entity_type.model_validate(json.loads(r[0])) for r in rows]

    def delete_entity(self, entity_type: type[BaseEntity], entity_id: str) -> None:
        self._guard_open()
        type_tag = _entity_type_name(entity_type)
        # First verify ownership; otherwise we'd silently no-op cross-workspace.
        owner = self._conn.execute(
            "SELECT workspace_id FROM entities WHERE entity_type = ? AND id = ?",
            (type_tag, entity_id),
        ).fetchone()
        if owner is None:
            raise EntityNotFoundError(type_tag, entity_id, self.workspace_id)
        if owner[0] != self.workspace_id:
            raise WorkspaceMismatchError(self.workspace_id, owner[0])
        self._conn.execute(
            "DELETE FROM entities WHERE entity_type = ? AND id = ?",
            (type_tag, entity_id),
        )

    def exists(self, entity_type: type[BaseEntity], entity_id: str) -> bool:
        self._guard_open()
        type_tag = _entity_type_name(entity_type)
        row = self._conn.execute(
            "SELECT 1 FROM entities WHERE entity_type = ? AND id = ? AND workspace_id = ?",
            (type_tag, entity_id, self.workspace_id),
        ).fetchone()
        return row is not None

    def _guard_open(self) -> None:
        if self._closed:
            raise RuntimeError("scope has been closed; open a new one")
