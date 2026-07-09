"""Mapper for ArenaRound — one parallel launch of an Arena's ready variants.

The schema field `index` maps to the SQL column `index_` (`index` collides
with common reserved-word tooling); `column_aliases` bridges that so
callers can still filter/order by the logical name `index`.
"""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from selfevals.schemas.arena import ArenaRound, RoundEntry
from selfevals.storage.postgres.mappers.base import (
    SHARED_COLUMNS,
    EntityMapper,
    register_mapper,
    shared_values,
)

_EXTRA_COLUMNS: tuple[str, ...] = (
    "arena_id",
    "index_",
    "entries",
    "reps",
    "state",
    "started_at",
    "finished_at",
    "summary",
)
_ALL_COLUMNS: tuple[str, ...] = (*SHARED_COLUMNS, *_EXTRA_COLUMNS)


class ArenaRoundMapper(EntityMapper[ArenaRound]):
    entity_cls = ArenaRound
    table = "arena_rounds"
    queryable_columns = frozenset({*SHARED_COLUMNS, "arena_id", "state", "index_"})
    column_aliases: dict[str, str] = {"index": "index_"}  # noqa: RUF012

    def upsert(self, cur: Any, entity: ArenaRound) -> None:
        e = entity
        values = [
            *shared_values(e),
            e.arena_id,
            e.index,
            Jsonb([entry.model_dump(mode="json") for entry in e.entries]),
            e.reps,
            str(e.state),
            e.started_at,
            e.finished_at,
            Jsonb(e.summary) if e.summary is not None else None,
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

    def load(self, cur: Any, workspace_id: str, entity_id: str) -> ArenaRound | None:
        cur.execute(
            f"SELECT {', '.join(_ALL_COLUMNS)} FROM {self.table} "
            "WHERE id = %s AND workspace_id = %s",
            (entity_id, workspace_id),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return _row_to_round(row)

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
    ) -> list[ArenaRound]:
        order_col = self._validate_order_by(order_by)
        clauses, params = self._scalar_where_sql(where)
        clauses.insert(0, "workspace_id = %s")
        params.insert(0, workspace_id)
        sql = (
            f"SELECT {', '.join(_ALL_COLUMNS)} FROM {self.table} "
            f"WHERE {' AND '.join(clauses)} "
            f"ORDER BY {order_col} {'DESC' if order_desc else 'ASC'}"
        )
        if limit is not None:
            sql += " LIMIT %s OFFSET %s"
            params.extend([limit, offset])
        cur.execute(sql, params)
        return [_row_to_round(row) for row in cur.fetchall()]


def _row_to_round(row: tuple[Any, ...]) -> ArenaRound:
    d = dict(zip(_ALL_COLUMNS, row, strict=True))
    return ArenaRound(
        id=d["id"],
        workspace_id=d["workspace_id"],
        version=d["version"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
        deleted_at=d["deleted_at"],
        arena_id=d["arena_id"],
        index=d["index_"],
        entries=[RoundEntry(**entry) for entry in d["entries"]],
        reps=d["reps"],
        state=d["state"],
        started_at=d["started_at"],
        finished_at=d["finished_at"],
        summary=d["summary"],
    )


register_mapper(ArenaRoundMapper())
