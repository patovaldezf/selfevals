"""Mapper for Dataset — a typed portfolio of EvalCase refs.

The fixed-shape `SplitAllocation` becomes flat prefixed columns (with the
free-form `other` map as JSONB); the variable-length `cases` list becomes the
`dataset_cases` child table; the optional `DatasetStatistics` becomes a
`stats_present` flag plus prefixed columns, its aggregate maps stored as JSONB.

``_build`` reconstructs the model in a single ``Dataset(...)`` constructor call
so the entity's custom ``__setattr__`` immutability guard never trips and the
cached ``statistics`` survives reconstruction.
"""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from selfevals.schemas._base import EntityRef
from selfevals.schemas.dataset import Dataset, DatasetStatistics, SplitAllocation
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
    "dataset_type",
    "source_dataset_id",
    "manifest_hash",
    "status",
    "split_optimization",
    "split_holdout",
    "split_reliability",
    "split_other",
    "stats_present",
    "stats_total_cases",
    "stats_by_level",
    "stats_by_feature",
    "stats_by_source",
    "stats_by_risk",
    "stats_holdout_count",
    "stats_pii_breakdown",
)
_ALL_COLUMNS: tuple[str, ...] = (*SHARED_COLUMNS, *_EXTRA_COLUMNS)


class DatasetMapper(EntityMapper[Dataset]):
    entity_cls = Dataset
    table = "datasets"
    queryable_columns = frozenset({*SHARED_COLUMNS, "name", "dataset_type", "status"})

    def upsert(self, cur: Any, entity: Dataset) -> None:
        e = entity
        split = e.split_allocation
        stats = e.statistics
        values = [
            *shared_values(e),
            e.name,
            e.description,
            e.dataset_type.value,
            e.source_dataset_id,
            e.manifest_hash,
            e.status.value,
            split.optimization,
            split.holdout,
            split.reliability,
            Jsonb(split.other),
            stats is not None,
            stats.total_cases if stats else None,
            Jsonb(stats.by_level) if stats else None,
            Jsonb(stats.by_feature) if stats else None,
            Jsonb(stats.by_source) if stats else None,
            Jsonb(stats.by_risk) if stats else None,
            stats.holdout_count if stats else None,
            Jsonb(stats.pii_breakdown) if stats else None,
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
        cur.execute("DELETE FROM dataset_cases WHERE dataset_id = %s", (e.id,))
        for pos, ref in enumerate(e.cases):
            cur.execute(
                "INSERT INTO dataset_cases "
                "(dataset_id, position, case_id, case_version) VALUES (%s, %s, %s, %s)",
                (e.id, pos, ref.id, ref.version),
            )

    def load(self, cur: Any, workspace_id: str, entity_id: str) -> Dataset | None:
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
    ) -> list[Dataset]:
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
        rows = cur.fetchall()
        return [self._build(cur, row) for row in rows]

    def _build(self, cur: Any, row: tuple[Any, ...]) -> Dataset:
        d = dict(zip(_ALL_COLUMNS, row, strict=True))
        cur.execute(
            "SELECT case_id, case_version FROM dataset_cases "
            "WHERE dataset_id = %s ORDER BY position",
            (d["id"],),
        )
        cases = [EntityRef(id=cid, version=cver) for cid, cver in cur.fetchall()]
        statistics = (
            DatasetStatistics(
                total_cases=d["stats_total_cases"],
                by_level=d["stats_by_level"],
                by_feature=d["stats_by_feature"],
                by_source=d["stats_by_source"],
                by_risk=d["stats_by_risk"],
                holdout_count=d["stats_holdout_count"],
                pii_breakdown=d["stats_pii_breakdown"],
            )
            if d["stats_present"]
            else None
        )
        # Build in a single constructor call so the entity's __setattr__
        # immutability guard never trips and `statistics` survives.
        return Dataset(
            id=d["id"],
            workspace_id=d["workspace_id"],
            version=d["version"],
            created_at=d["created_at"],
            updated_at=d["updated_at"],
            deleted_at=d["deleted_at"],
            name=d["name"],
            description=d["description"],
            dataset_type=d["dataset_type"],
            cases=cases,
            split_allocation=SplitAllocation(
                optimization=d["split_optimization"],
                holdout=d["split_holdout"],
                reliability=d["split_reliability"],
                other=d["split_other"],
            ),
            source_dataset_id=d["source_dataset_id"],
            manifest_hash=d["manifest_hash"],
            status=d["status"],
            statistics=statistics,
        )


register_mapper(DatasetMapper())
