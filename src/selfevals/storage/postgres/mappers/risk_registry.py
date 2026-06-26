"""Mapper for RiskRegistry — the workspace-wide risk taxonomy.

The main table carries only the shared columns; the variable-length
``dimensions`` list lives in the ``risk_registry_dimensions`` child table.
Child rows are deleted and reinserted on every upsert. ``load`` reassembles
the dimensions in declared order.
"""

from __future__ import annotations

from typing import Any

from selfevals.schemas.registry import RiskDimension, RiskRegistry
from selfevals.storage.postgres.mappers.base import (
    SHARED_COLUMNS,
    EntityMapper,
    register_mapper,
    shared_values,
)

_ALL_COLUMNS: tuple[str, ...] = SHARED_COLUMNS


class RiskRegistryMapper(EntityMapper[RiskRegistry]):
    entity_cls = RiskRegistry
    table = "risk_registries"
    queryable_columns = frozenset(SHARED_COLUMNS)

    def upsert(self, cur: Any, entity: RiskRegistry) -> None:
        e = entity
        values = [*shared_values(e)]
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
            "DELETE FROM risk_registry_dimensions WHERE risk_registry_id = %s",
            (e.id,),
        )
        for pos, dim in enumerate(e.dimensions):
            cur.execute(
                "INSERT INTO risk_registry_dimensions "
                "(risk_registry_id, position, name, levels) VALUES (%s, %s, %s, %s)",
                (e.id, pos, dim.name, list(dim.levels)),
            )

    def load(self, cur: Any, workspace_id: str, entity_id: str) -> RiskRegistry | None:
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
    ) -> list[RiskRegistry]:
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

    def _build(self, cur: Any, row: tuple[Any, ...]) -> RiskRegistry:
        d = dict(zip(_ALL_COLUMNS, row, strict=True))
        rid = d["id"]
        cur.execute(
            "SELECT name, levels FROM risk_registry_dimensions "
            "WHERE risk_registry_id = %s ORDER BY position",
            (rid,),
        )
        dimensions = [
            RiskDimension(name=name, levels=levels) for name, levels in cur.fetchall()
        ]
        return RiskRegistry(
            id=d["id"],
            workspace_id=d["workspace_id"],
            version=d["version"],
            created_at=d["created_at"],
            updated_at=d["updated_at"],
            deleted_at=d["deleted_at"],
            dimensions=dimensions,
        )


register_mapper(RiskRegistryMapper())
