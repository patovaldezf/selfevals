"""Mapper for FailureMode — a taxonomy entry plus its evidence examples.

The scalar fields become flat columns on ``failure_modes``; the variable-length
``examples`` list becomes child rows in ``failure_mode_examples`` (DELETE +
reinsert on upsert, like the experiment mapper). ``load`` reassembles the full
nested Pydantic model.
"""

from __future__ import annotations

from typing import Any

from selfevals.schemas.failure_mode import FailureMode, FailureModeExample
from selfevals.storage.postgres.mappers.base import (
    SHARED_COLUMNS,
    EntityMapper,
    register_mapper,
    shared_values,
)

_EXTRA_COLUMNS: tuple[str, ...] = (
    "slug",
    "title",
    "definition",
    "status",
    "parent_mode_id",
    "proposed_by",
    "first_seen_iteration",
    "superseded_by",
)
_ALL_COLUMNS: tuple[str, ...] = (*SHARED_COLUMNS, *_EXTRA_COLUMNS)


class FailureModeMapper(EntityMapper[FailureMode]):
    entity_cls = FailureMode
    table = "failure_modes"
    queryable_columns = frozenset({*SHARED_COLUMNS, "slug", "status"})

    def upsert(self, cur: Any, entity: FailureMode) -> None:
        e = entity
        values = [
            *shared_values(e),
            e.slug,
            e.title,
            e.definition,
            e.status.value,
            e.parent_mode_id,
            e.proposed_by,
            e.first_seen_iteration,
            e.superseded_by,
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
        # Replace child rows (idempotent on update).
        cur.execute(
            "DELETE FROM failure_mode_examples WHERE failure_mode_id = %s", (e.id,)
        )
        for pos, ex in enumerate(e.examples):
            cur.execute(
                "INSERT INTO failure_mode_examples "
                "(failure_mode_id, position, trace_id, quote_pointer, quote_hash, note) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (e.id, pos, ex.trace_id, ex.quote_pointer, ex.quote_hash, ex.note),
            )

    def load(self, cur: Any, workspace_id: str, entity_id: str) -> FailureMode | None:
        cur.execute(
            f"SELECT {', '.join(_ALL_COLUMNS)} FROM {self.table} "
            "WHERE id = %s AND workspace_id = %s",
            (entity_id, workspace_id),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return self._build(cur, row)

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
    ) -> list[FailureMode]:
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
        return [self._build(cur, row) for row in cur.fetchall()]

    def _build(self, cur: Any, row: tuple[Any, ...]) -> FailureMode:
        d = dict(zip(_ALL_COLUMNS, row, strict=True))
        cur.execute(
            "SELECT trace_id, quote_pointer, quote_hash, note "
            "FROM failure_mode_examples WHERE failure_mode_id = %s ORDER BY position",
            (d["id"],),
        )
        examples = [
            FailureModeExample(
                trace_id=trace_id,
                quote_pointer=quote_pointer,
                quote_hash=quote_hash,
                note=note,
            )
            for trace_id, quote_pointer, quote_hash, note in cur.fetchall()
        ]
        return FailureMode(
            id=d["id"],
            workspace_id=d["workspace_id"],
            version=d["version"],
            created_at=d["created_at"],
            updated_at=d["updated_at"],
            deleted_at=d["deleted_at"],
            slug=d["slug"],
            title=d["title"],
            definition=d["definition"],
            status=d["status"],
            parent_mode_id=d["parent_mode_id"],
            examples=examples,
            proposed_by=d["proposed_by"],
            first_seen_iteration=d["first_seen_iteration"],
            superseded_by=d["superseded_by"],
        )


register_mapper(FailureModeMapper())
