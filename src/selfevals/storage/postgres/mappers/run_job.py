"""Mapper for RunJob — durable worker-backed run execution envelope.

A flat row: status enum -> stored value, spec_payload -> JSONB, all the
lease/timing fields pass through as scalars. No child tables.
"""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from selfevals.schemas.job import RunJob
from selfevals.storage.postgres.mappers.base import (
    SHARED_COLUMNS,
    EntityMapper,
    register_mapper,
    shared_values,
)

_JOB_COLUMNS: tuple[str, ...] = (
    *SHARED_COLUMNS,
    "experiment_id",
    "status",
    "attempt",
    "max_attempts",
    "lease_owner",
    "lease_expires_at",
    "cancel_requested_at",
    "started_at",
    "finished_at",
    "last_error",
    "spec_payload",
    "reps",
)


class RunJobMapper(EntityMapper[RunJob]):
    entity_cls = RunJob
    table = "run_jobs"
    queryable_columns = frozenset({*SHARED_COLUMNS, "experiment_id", "status"})

    def upsert(self, cur: Any, entity: RunJob) -> None:
        e = entity
        values = [
            *shared_values(e),
            e.experiment_id,
            e.status.value,
            e.attempt,
            e.max_attempts,
            e.lease_owner,
            e.lease_expires_at,
            e.cancel_requested_at,
            e.started_at,
            e.finished_at,
            e.last_error,
            Jsonb(e.spec_payload),
            e.reps,
        ]
        placeholders = ", ".join(["%s"] * len(_JOB_COLUMNS))
        updates = ", ".join(
            f"{col} = EXCLUDED.{col}"
            for col in _JOB_COLUMNS
            if col not in ("id", "created_at")
        )
        cur.execute(
            f"""
            INSERT INTO {self.table} ({", ".join(_JOB_COLUMNS)})
            VALUES ({placeholders})
            ON CONFLICT (id) DO UPDATE SET {updates}
            """,
            values,
        )

    def load(self, cur: Any, workspace_id: str, entity_id: str) -> RunJob | None:
        cur.execute(
            f"SELECT {', '.join(_JOB_COLUMNS)} FROM {self.table} "
            "WHERE id = %s AND workspace_id = %s",
            (entity_id, workspace_id),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return _row_to_run_job(row)

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
    ) -> list[RunJob]:
        self._validate_order_by(order_by)
        clauses, params = self._scalar_where_sql(where)
        clauses.insert(0, "workspace_id = %s")
        params.insert(0, workspace_id)
        sql = (
            f"SELECT {', '.join(_JOB_COLUMNS)} FROM {self.table} "
            f"WHERE {' AND '.join(clauses)} "
            f"ORDER BY {order_by} {'DESC' if order_desc else 'ASC'}"
        )
        if limit is not None:
            sql += " LIMIT %s OFFSET %s"
            params.extend([limit, offset])
        cur.execute(sql, params)
        return [_row_to_run_job(row) for row in cur.fetchall()]


def _row_to_run_job(row: tuple[Any, ...]) -> RunJob:
    d = dict(zip(_JOB_COLUMNS, row, strict=True))
    return RunJob(
        id=d["id"],
        workspace_id=d["workspace_id"],
        version=d["version"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
        deleted_at=d["deleted_at"],
        experiment_id=d["experiment_id"],
        status=d["status"],
        attempt=d["attempt"],
        max_attempts=d["max_attempts"],
        lease_owner=d["lease_owner"],
        lease_expires_at=d["lease_expires_at"],
        cancel_requested_at=d["cancel_requested_at"],
        started_at=d["started_at"],
        finished_at=d["finished_at"],
        last_error=d["last_error"],
        spec_payload=d["spec_payload"],
        reps=d["reps"],
    )


register_mapper(RunJobMapper())
