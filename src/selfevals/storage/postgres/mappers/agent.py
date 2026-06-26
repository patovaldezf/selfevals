"""Mapper for Agent — one configurable unit an experiment runs.

The fixed-shape `ModelRef` becomes flat `model_provider`/`model_name` columns;
`tools`/`features`/`modalities` become TEXT[] arrays; the free-form
`parameters` dict becomes a JSONB column. No child tables.
"""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from selfevals.schemas.fleet import Agent, ModelRef
from selfevals.storage.postgres.mappers.base import (
    SHARED_COLUMNS,
    EntityMapper,
    register_mapper,
    shared_values,
)

# Main-table columns after the shared ones, in insert order.
_EXTRA_COLUMNS: tuple[str, ...] = (
    "fleet_id",
    "agent_type",
    "model_provider",
    "model_name",
    "system_prompt_pointer",
    "graph_definition_pointer",
    "handoff_target_id",
    "tools",
    "features",
    "parameters",
    "modalities",
    "content_hash",
    "status",
)
_ALL_COLUMNS: tuple[str, ...] = (*SHARED_COLUMNS, *_EXTRA_COLUMNS)


class AgentMapper(EntityMapper[Agent]):
    entity_cls = Agent
    table = "agents"
    queryable_columns = frozenset({*SHARED_COLUMNS, "fleet_id", "agent_type", "status"})

    def upsert(self, cur: Any, entity: Agent) -> None:
        e = entity
        values = [
            *shared_values(e),
            e.fleet_id,
            e.agent_type.value,
            e.model.provider,
            e.model.name,
            e.system_prompt_pointer,
            e.graph_definition_pointer,
            e.handoff_target_id,
            list(e.tools),
            list(e.features),
            Jsonb(e.parameters),
            [m.value for m in e.modalities],
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

    def load(self, cur: Any, workspace_id: str, entity_id: str) -> Agent | None:
        cur.execute(
            f"SELECT {', '.join(_ALL_COLUMNS)} FROM {self.table} "
            "WHERE id = %s AND workspace_id = %s",
            (entity_id, workspace_id),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return _row_to_agent(row)

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
    ) -> list[Agent]:
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
        return [_row_to_agent(row) for row in cur.fetchall()]


def _row_to_agent(row: tuple[Any, ...]) -> Agent:
    d = dict(zip(_ALL_COLUMNS, row, strict=True))
    return Agent(
        id=d["id"],
        workspace_id=d["workspace_id"],
        version=d["version"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
        deleted_at=d["deleted_at"],
        fleet_id=d["fleet_id"],
        agent_type=d["agent_type"],
        model=ModelRef(provider=d["model_provider"], name=d["model_name"]),
        system_prompt_pointer=d["system_prompt_pointer"],
        graph_definition_pointer=d["graph_definition_pointer"],
        handoff_target_id=d["handoff_target_id"],
        tools=d["tools"],
        features=d["features"],
        parameters=d["parameters"],
        modalities=d["modalities"],
        content_hash=d["content_hash"],
        status=d["status"],
    )


register_mapper(AgentMapper())
