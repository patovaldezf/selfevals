"""Mappers for the analysis entities: HypothesisRecord, AnalysisStagingRecord."""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from selfevals.analysis.hypothesis import HypothesisRecord
from selfevals.analysis.staging import AnalysisStagingRecord
from selfevals.storage.postgres.mappers.base import (
    SHARED_COLUMNS,
    EntityMapper,
    register_mapper,
    shared_values,
)

_HYP_COLUMNS: tuple[str, ...] = (
    *SHARED_COLUMNS,
    "experiment_id",
    "targets_mode_slug",
    "statement",
    "suggested_parameters",
    "consumed_by_iteration",
)


class HypothesisRecordMapper(EntityMapper[HypothesisRecord]):
    entity_cls = HypothesisRecord
    table = "hypothesis_records"
    queryable_columns = frozenset({*SHARED_COLUMNS, "experiment_id", "targets_mode_slug"})

    def upsert(self, cur: Any, entity: HypothesisRecord) -> None:
        values = [
            *shared_values(entity),
            entity.experiment_id,
            entity.targets_mode_slug,
            entity.statement,
            Jsonb(entity.suggested_parameters),
            entity.consumed_by_iteration,
        ]
        placeholders = ", ".join(["%s"] * len(_HYP_COLUMNS))
        updates = ", ".join(
            f"{c} = EXCLUDED.{c}" for c in _HYP_COLUMNS if c not in ("id", "created_at")
        )
        cur.execute(
            f"""
            INSERT INTO {self.table} ({", ".join(_HYP_COLUMNS)})
            VALUES ({placeholders})
            ON CONFLICT (id) DO UPDATE SET {updates}
            """,
            values,
        )

    def load(self, cur: Any, workspace_id: str, entity_id: str) -> HypothesisRecord | None:
        cur.execute(
            f"SELECT {', '.join(_HYP_COLUMNS)} FROM {self.table} "
            "WHERE id = %s AND workspace_id = %s",
            (entity_id, workspace_id),
        )
        row = cur.fetchone()
        return _row_to_hypothesis(row) if row is not None else None

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
    ) -> list[HypothesisRecord]:
        self._validate_order_by(order_by)
        clauses, params = self._scalar_where_sql(where)
        clauses.insert(0, "workspace_id = %s")
        params.insert(0, workspace_id)
        sql = (
            f"SELECT {', '.join(_HYP_COLUMNS)} FROM {self.table} "
            f"WHERE {' AND '.join(clauses)} "
            f"ORDER BY {order_by} {'DESC' if order_desc else 'ASC'}"
        )
        if limit is not None:
            sql += " LIMIT %s OFFSET %s"
            params.extend([limit, offset])
        cur.execute(sql, params)
        return [_row_to_hypothesis(row) for row in cur.fetchall()]


def _row_to_hypothesis(row: tuple[Any, ...]) -> HypothesisRecord:
    d = dict(zip(_HYP_COLUMNS, row, strict=True))
    return HypothesisRecord(
        id=d["id"],
        workspace_id=d["workspace_id"],
        version=d["version"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
        deleted_at=d["deleted_at"],
        experiment_id=d["experiment_id"],
        targets_mode_slug=d["targets_mode_slug"],
        statement=d["statement"],
        suggested_parameters=d["suggested_parameters"],
        consumed_by_iteration=d["consumed_by_iteration"],
    )


_STG_COLUMNS: tuple[str, ...] = (
    *SHARED_COLUMNS,
    "experiment_id",
    "iteration",
    "fail_rate",
    "threshold",
    "scope",
    "reason",
    "consumed",
)


class AnalysisStagingRecordMapper(EntityMapper[AnalysisStagingRecord]):
    entity_cls = AnalysisStagingRecord
    table = "analysis_staging_records"
    queryable_columns = frozenset({*SHARED_COLUMNS, "experiment_id", "iteration", "consumed"})

    def upsert(self, cur: Any, entity: AnalysisStagingRecord) -> None:
        values = [
            *shared_values(entity),
            entity.experiment_id,
            entity.iteration,
            entity.fail_rate,
            entity.threshold,
            entity.scope,
            entity.reason,
            entity.consumed,
        ]
        placeholders = ", ".join(["%s"] * len(_STG_COLUMNS))
        updates = ", ".join(
            f"{c} = EXCLUDED.{c}" for c in _STG_COLUMNS if c not in ("id", "created_at")
        )
        cur.execute(
            f"""
            INSERT INTO {self.table} ({", ".join(_STG_COLUMNS)})
            VALUES ({placeholders})
            ON CONFLICT (id) DO UPDATE SET {updates}
            """,
            values,
        )

    def load(self, cur: Any, workspace_id: str, entity_id: str) -> AnalysisStagingRecord | None:
        cur.execute(
            f"SELECT {', '.join(_STG_COLUMNS)} FROM {self.table} "
            "WHERE id = %s AND workspace_id = %s",
            (entity_id, workspace_id),
        )
        row = cur.fetchone()
        return _row_to_staging(row) if row is not None else None

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
    ) -> list[AnalysisStagingRecord]:
        self._validate_order_by(order_by)
        clauses, params = self._scalar_where_sql(where)
        clauses.insert(0, "workspace_id = %s")
        params.insert(0, workspace_id)
        sql = (
            f"SELECT {', '.join(_STG_COLUMNS)} FROM {self.table} "
            f"WHERE {' AND '.join(clauses)} "
            f"ORDER BY {order_by} {'DESC' if order_desc else 'ASC'}"
        )
        if limit is not None:
            sql += " LIMIT %s OFFSET %s"
            params.extend([limit, offset])
        cur.execute(sql, params)
        return [_row_to_staging(row) for row in cur.fetchall()]


def _row_to_staging(row: tuple[Any, ...]) -> AnalysisStagingRecord:
    d = dict(zip(_STG_COLUMNS, row, strict=True))
    return AnalysisStagingRecord(
        id=d["id"],
        workspace_id=d["workspace_id"],
        version=d["version"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
        deleted_at=d["deleted_at"],
        experiment_id=d["experiment_id"],
        iteration=d["iteration"],
        fail_rate=d["fail_rate"],
        threshold=d["threshold"],
        scope=d["scope"],
        reason=d["reason"],
        consumed=d["consumed"],
    )


register_mapper(HypothesisRecordMapper())
register_mapper(AnalysisStagingRecordMapper())
