"""Mapper for Arena — a code-variant competition over one dataset+graders.

`agent_command`, `agent_env`, `spec_template`, `budget` are JSONB; everything
else is scalar.
"""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from selfevals.schemas.arena import Arena, ArenaBudget
from selfevals.storage.postgres.mappers.base import (
    SHARED_COLUMNS,
    EntityMapper,
    register_mapper,
    shared_values,
)

_EXTRA_COLUMNS: tuple[str, ...] = (
    "name",
    "goal",
    "repo_path",
    "agent_command",
    "agent_env",
    "spec_template",
    "dataset_id",
    "objective_metric",
    "budget",
    "current_round",
    "state",
    "winner_variant_id",
)
_ALL_COLUMNS: tuple[str, ...] = (*SHARED_COLUMNS, *_EXTRA_COLUMNS)


class ArenaMapper(EntityMapper[Arena]):
    entity_cls = Arena
    table = "arenas"
    queryable_columns = frozenset({*SHARED_COLUMNS, "state", "dataset_id"})

    def upsert(self, cur: Any, entity: Arena) -> None:
        e = entity
        values = [
            *shared_values(e),
            e.name,
            e.goal,
            e.repo_path,
            Jsonb(list(e.agent_command)),
            Jsonb(e.agent_env) if e.agent_env is not None else None,
            Jsonb(e.spec_template),
            e.dataset_id,
            e.objective_metric,
            Jsonb(e.budget.model_dump(mode="json")),
            e.current_round,
            str(e.state),
            e.winner_variant_id,
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

    def load(self, cur: Any, workspace_id: str, entity_id: str) -> Arena | None:
        cur.execute(
            f"SELECT {', '.join(_ALL_COLUMNS)} FROM {self.table} "
            "WHERE id = %s AND workspace_id = %s",
            (entity_id, workspace_id),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return _row_to_arena(row)

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
    ) -> list[Arena]:
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
        return [_row_to_arena(row) for row in cur.fetchall()]


def _row_to_arena(row: tuple[Any, ...]) -> Arena:
    d = dict(zip(_ALL_COLUMNS, row, strict=True))
    return Arena(
        id=d["id"],
        workspace_id=d["workspace_id"],
        version=d["version"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
        deleted_at=d["deleted_at"],
        name=d["name"],
        goal=d["goal"],
        repo_path=d["repo_path"],
        agent_command=list(d["agent_command"]),
        agent_env=d["agent_env"],
        spec_template=d["spec_template"],
        dataset_id=d["dataset_id"],
        objective_metric=d["objective_metric"],
        budget=ArenaBudget(**d["budget"]),
        current_round=d["current_round"],
        state=d["state"],
        winner_variant_id=d["winner_variant_id"],
    )


register_mapper(ArenaMapper())
