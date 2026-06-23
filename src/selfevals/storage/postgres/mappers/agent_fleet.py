"""Mapper for AgentFleet — a snapshot of bound agents/tools/features.

The fixed scalar fields become flat columns; `features` becomes a TEXT[]
array; the free-form `feature_params` dict becomes a JSONB column; the
variable-length `agents`/`tools` EntityRef lists become rows in the
`agent_fleet_refs` child table (DELETE + reinsert on upsert).
"""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from selfevals.schemas._base import EntityRef
from selfevals.schemas.fleet import AgentFleet
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
    "features",
    "feature_params",
    "content_hash",
    "status",
)
_ALL_COLUMNS: tuple[str, ...] = (*SHARED_COLUMNS, *_EXTRA_COLUMNS)


class AgentFleetMapper(EntityMapper[AgentFleet]):
    entity_cls = AgentFleet
    table = "agent_fleets"
    queryable_columns = frozenset({*SHARED_COLUMNS, "name", "status"})

    def upsert(self, cur: Any, entity: AgentFleet) -> None:
        e = entity
        values = [
            *shared_values(e),
            e.name,
            e.description,
            list(e.features),
            Jsonb(e.feature_params),
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
        # Replace child rows (idempotent on update).
        cur.execute("DELETE FROM agent_fleet_refs WHERE fleet_id = %s", (e.id,))
        for kind, refs in (("agent", e.agents), ("tool", e.tools)):
            for pos, ref in enumerate(refs):
                cur.execute(
                    "INSERT INTO agent_fleet_refs "
                    "(fleet_id, ref_kind, position, ref_id, ref_version) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (e.id, kind, pos, ref.id, ref.version),
                )

    def load(self, cur: Any, workspace_id: str, entity_id: str) -> AgentFleet | None:
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
    ) -> list[AgentFleet]:
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

    def _build(self, cur: Any, row: tuple[Any, ...]) -> AgentFleet:
        d = dict(zip(_ALL_COLUMNS, row, strict=True))
        fid = d["id"]
        cur.execute(
            "SELECT ref_kind, ref_id, ref_version FROM agent_fleet_refs "
            "WHERE fleet_id = %s ORDER BY ref_kind, position",
            (fid,),
        )
        refs_by_kind: dict[str, list[EntityRef]] = {"agent": [], "tool": []}
        for kind, ref_id, ref_ver in cur.fetchall():
            refs_by_kind[kind].append(EntityRef(id=ref_id, version=ref_ver))
        return AgentFleet(
            id=d["id"],
            workspace_id=d["workspace_id"],
            version=d["version"],
            created_at=d["created_at"],
            updated_at=d["updated_at"],
            deleted_at=d["deleted_at"],
            name=d["name"],
            description=d["description"],
            agents=refs_by_kind["agent"],
            tools=refs_by_kind["tool"],
            features=d["features"],
            feature_params=d["feature_params"],
            content_hash=d["content_hash"],
            status=d["status"],
        )


register_mapper(AgentFleetMapper())
