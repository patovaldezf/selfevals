"""Mapper for DatasetBaseline — the fixed reference a dataset is gated against.

All fields are scalar except `confusion`, a serialized confusion report stored
as JSONB. No child tables.
"""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from selfevals.schemas.dataset import DatasetBaseline
from selfevals.storage.postgres.mappers.base import (
    SHARED_COLUMNS,
    EntityMapper,
    register_mapper,
    shared_values,
)

# Main-table columns after the shared ones, in insert order.
_EXTRA_COLUMNS: tuple[str, ...] = (
    "dataset_id",
    "iteration_id",
    "experiment_id",
    "primary_metric",
    "primary_value",
    "error_rate",
    "confusion",
)
_ALL_COLUMNS: tuple[str, ...] = (*SHARED_COLUMNS, *_EXTRA_COLUMNS)


class DatasetBaselineMapper(EntityMapper[DatasetBaseline]):
    entity_cls = DatasetBaseline
    table = "dataset_baselines"
    queryable_columns = frozenset({*SHARED_COLUMNS, "dataset_id", "experiment_id"})

    def upsert(self, cur: Any, entity: DatasetBaseline) -> None:
        e = entity
        values = [
            *shared_values(e),
            e.dataset_id,
            e.iteration_id,
            e.experiment_id,
            e.primary_metric,
            e.primary_value,
            e.error_rate,
            Jsonb(e.confusion) if e.confusion is not None else None,
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

    def load(self, cur: Any, workspace_id: str, entity_id: str) -> DatasetBaseline | None:
        cur.execute(
            f"SELECT {', '.join(_ALL_COLUMNS)} FROM {self.table} "
            "WHERE id = %s AND workspace_id = %s",
            (entity_id, workspace_id),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return _row_to_baseline(row)

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
    ) -> list[DatasetBaseline]:
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
        return [_row_to_baseline(row) for row in cur.fetchall()]


def _row_to_baseline(row: tuple[Any, ...]) -> DatasetBaseline:
    d = dict(zip(_ALL_COLUMNS, row, strict=True))
    return DatasetBaseline(
        id=d["id"],
        workspace_id=d["workspace_id"],
        version=d["version"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
        deleted_at=d["deleted_at"],
        dataset_id=d["dataset_id"],
        iteration_id=d["iteration_id"],
        experiment_id=d["experiment_id"],
        primary_metric=d["primary_metric"],
        primary_value=d["primary_value"],
        error_rate=d["error_rate"],
        confusion=d["confusion"],
    )


register_mapper(DatasetBaselineMapper())
