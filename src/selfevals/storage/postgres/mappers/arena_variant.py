"""Mapper for ArenaVariant — one code variant (git ref + worktree) in an Arena."""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from selfevals.schemas.arena import ArenaVariant
from selfevals.storage.postgres.mappers.base import (
    SHARED_COLUMNS,
    EntityMapper,
    register_mapper,
    shared_values,
)

_EXTRA_COLUMNS: tuple[str, ...] = (
    "arena_id",
    "name",
    "git_ref",
    "resolved_sha",
    "worktree_path",
    "setup_command",
    "env_overrides",
    "hypothesis",
    "created_in_round",
    "state",
    "error",
)
_ALL_COLUMNS: tuple[str, ...] = (*SHARED_COLUMNS, *_EXTRA_COLUMNS)


class ArenaVariantMapper(EntityMapper[ArenaVariant]):
    entity_cls = ArenaVariant
    table = "arena_variants"
    queryable_columns = frozenset({*SHARED_COLUMNS, "arena_id", "state"})

    def upsert(self, cur: Any, entity: ArenaVariant) -> None:
        e = entity
        values = [
            *shared_values(e),
            e.arena_id,
            e.name,
            e.git_ref,
            e.resolved_sha,
            e.worktree_path,
            Jsonb(list(e.setup_command)) if e.setup_command is not None else None,
            Jsonb(e.env_overrides) if e.env_overrides is not None else None,
            e.hypothesis,
            e.created_in_round,
            str(e.state),
            e.error,
        ]
        placeholders = ", ".join(["%s"] * len(_ALL_COLUMNS))
        updates = ", ".join(
            f"{c} = EXCLUDED.{c}" for c in _ALL_COLUMNS if c not in ("id", "created_at")
        )
        cur.execute(
            f"""
            INSERT INTO {self.table} ({", ".join(_ALL_COLUMNS)})
            VALUES ({placeholders})
            ON CONFLICT (id) DO UPDATE SET {updates}
            """,
            values,
        )

    def load(self, cur: Any, workspace_id: str, entity_id: str) -> ArenaVariant | None:
        cur.execute(
            f"SELECT {', '.join(_ALL_COLUMNS)} FROM {self.table} "
            "WHERE id = %s AND workspace_id = %s",
            (entity_id, workspace_id),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return _row_to_variant(row)

    def load_many(
        self,
        cur: Any,
        *,
        workspace_id: str,
        where: dict[str, Any],
        order_by: str,
        order_desc: bool,
        limit: int | None,
        offset: int,
    ) -> list[ArenaVariant]:
        self._validate_order_by(order_by)
        clauses, params = self._scalar_where_sql(where)
        clauses.insert(0, "workspace_id = %s")
        params.insert(0, workspace_id)
        sql = (
            f"SELECT {', '.join(_ALL_COLUMNS)} FROM {self.table} "
            f"WHERE {' AND '.join(clauses)} "
            f"ORDER BY {order_by} {'DESC' if order_desc else 'ASC'}"
        )
        if limit is not None:
            sql += " LIMIT %s OFFSET %s"
            params.extend([limit, offset])
        cur.execute(sql, params)
        return [_row_to_variant(row) for row in cur.fetchall()]


def _row_to_variant(row: tuple[Any, ...]) -> ArenaVariant:
    d = dict(zip(_ALL_COLUMNS, row, strict=True))
    return ArenaVariant(
        id=d["id"],
        workspace_id=d["workspace_id"],
        version=d["version"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
        deleted_at=d["deleted_at"],
        arena_id=d["arena_id"],
        name=d["name"],
        git_ref=d["git_ref"],
        resolved_sha=d["resolved_sha"],
        worktree_path=d["worktree_path"],
        setup_command=list(d["setup_command"]) if d["setup_command"] is not None else None,
        env_overrides=d["env_overrides"],
        hypothesis=d["hypothesis"],
        created_in_round=d["created_in_round"],
        state=d["state"],
        error=d["error"],
    )


register_mapper(ArenaVariantMapper())
