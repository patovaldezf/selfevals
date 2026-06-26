"""Mapper for Tool — a first-class entity an Agent can invoke.

The opaque `ToolSchema` (input/output JSON-Schema dicts) becomes two JSONB
columns; the remaining scalars map directly. No child tables.
"""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from selfevals.schemas.tool import Tool, ToolSchema
from selfevals.storage.postgres.mappers.base import (
    SHARED_COLUMNS,
    EntityMapper,
    register_mapper,
    shared_values,
)

# Main-table columns after the shared ones, in insert order.
_EXTRA_COLUMNS: tuple[str, ...] = (
    "name",
    "description",
    "schema_input",
    "schema_output",
    "code_pointer",
    "side_effects",
    "content_hash",
    "status",
)
_ALL_COLUMNS: tuple[str, ...] = (*SHARED_COLUMNS, *_EXTRA_COLUMNS)


class ToolMapper(EntityMapper[Tool]):
    entity_cls = Tool
    table = "tools"
    queryable_columns = frozenset({*SHARED_COLUMNS, "name", "status"})

    def upsert(self, cur: Any, entity: Tool) -> None:
        e = entity
        schema_output = (
            Jsonb(e.schema_.output_schema) if e.schema_.output_schema is not None else None
        )
        values = [
            *shared_values(e),
            e.name,
            e.description,
            Jsonb(e.schema_.input_schema),
            schema_output,
            e.code_pointer,
            e.side_effects,
            e.content_hash,
            e.status.value,
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

    def load(self, cur: Any, workspace_id: str, entity_id: str) -> Tool | None:
        cur.execute(
            f"SELECT {', '.join(_ALL_COLUMNS)} FROM {self.table} "
            "WHERE id = %s AND workspace_id = %s",
            (entity_id, workspace_id),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return _row_to_tool(row)

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
    ) -> list[Tool]:
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
        return [_row_to_tool(row) for row in cur.fetchall()]


def _row_to_tool(row: tuple[Any, ...]) -> Tool:
    d = dict(zip(_ALL_COLUMNS, row, strict=True))
    return Tool(
        id=d["id"],
        workspace_id=d["workspace_id"],
        version=d["version"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
        deleted_at=d["deleted_at"],
        name=d["name"],
        description=d["description"],
        schema=ToolSchema(
            input_schema=d["schema_input"],
            output_schema=d["schema_output"],
        ),
        code_pointer=d["code_pointer"],
        side_effects=d["side_effects"],
        content_hash=d["content_hash"],
        status=d["status"],
    )


register_mapper(ToolMapper())
