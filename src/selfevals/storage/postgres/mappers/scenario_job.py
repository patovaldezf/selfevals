"""Mapper for ScenarioJob — one sharded (iteration, case) unit of a run.

A flat row mirroring RunJobMapper: status enum -> stored value,
parameter_overrides -> JSONB, lease/timing fields pass through as scalars.
No child tables. The atomic SKIP-LOCKED claim does NOT go through this mapper's
``upsert`` (that path is the version-CAS write); it issues a direct UPDATE in
``queries.py`` instead.
"""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from selfevals.schemas.job import ScenarioJob
from selfevals.storage.postgres.mappers.base import (
    SHARED_COLUMNS,
    EntityMapper,
    register_mapper,
    shared_values,
)

_SCENARIO_COLUMNS: tuple[str, ...] = (
    *SHARED_COLUMNS,
    "run_job_id",
    "experiment_id",
    "iteration",
    "case_id",
    "reps",
    "status",
    "attempt",
    "max_attempts",
    "lease_until",
    "worker_id",
    "error",
    "parameter_overrides",
    "started_at",
    "finished_at",
)


class ScenarioJobMapper(EntityMapper[ScenarioJob]):
    entity_cls = ScenarioJob
    table = "scenario_jobs"
    queryable_columns = frozenset(
        {*SHARED_COLUMNS, "run_job_id", "experiment_id", "iteration", "case_id", "status"}
    )

    def upsert(self, cur: Any, entity: ScenarioJob) -> None:
        e = entity
        values = [
            *shared_values(e),
            e.run_job_id,
            e.experiment_id,
            e.iteration,
            e.case_id,
            e.reps,
            e.status.value,
            e.attempt,
            e.max_attempts,
            e.lease_until,
            e.worker_id,
            e.error,
            Jsonb(e.parameter_overrides),
            e.started_at,
            e.finished_at,
        ]
        placeholders = ", ".join(["%s"] * len(_SCENARIO_COLUMNS))
        updates = ", ".join(
            f"{col} = EXCLUDED.{col}"
            for col in _SCENARIO_COLUMNS
            if col not in ("id", "created_at")
        )
        cur.execute(
            f"""
            INSERT INTO {self.table} ({", ".join(_SCENARIO_COLUMNS)})
            VALUES ({placeholders})
            ON CONFLICT (id) DO UPDATE SET {updates}
            """,
            values,
        )

    def load(self, cur: Any, workspace_id: str, entity_id: str) -> ScenarioJob | None:
        cur.execute(
            f"SELECT {', '.join(_SCENARIO_COLUMNS)} FROM {self.table} "
            "WHERE id = %s AND workspace_id = %s",
            (entity_id, workspace_id),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return _row_to_scenario_job(row)

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
    ) -> list[ScenarioJob]:
        self._validate_order_by(order_by)
        clauses, params = self._scalar_where_sql(where)
        clauses.insert(0, "workspace_id = %s")
        params.insert(0, workspace_id)
        sql = (
            f"SELECT {', '.join(_SCENARIO_COLUMNS)} FROM {self.table} "
            f"WHERE {' AND '.join(clauses)} "
            f"ORDER BY {order_by} {'DESC' if order_desc else 'ASC'}"
        )
        if limit is not None:
            sql += " LIMIT %s OFFSET %s"
            params.extend([limit, offset])
        cur.execute(sql, params)
        return [_row_to_scenario_job(row) for row in cur.fetchall()]


def _row_to_scenario_job(row: tuple[Any, ...]) -> ScenarioJob:
    d = dict(zip(_SCENARIO_COLUMNS, row, strict=True))
    return ScenarioJob(
        id=d["id"],
        workspace_id=d["workspace_id"],
        version=d["version"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
        deleted_at=d["deleted_at"],
        run_job_id=d["run_job_id"],
        experiment_id=d["experiment_id"],
        iteration=d["iteration"],
        case_id=d["case_id"],
        reps=d["reps"],
        status=d["status"],
        attempt=d["attempt"],
        max_attempts=d["max_attempts"],
        lease_until=d["lease_until"],
        worker_id=d["worker_id"],
        error=d["error"],
        parameter_overrides=d["parameter_overrides"],
        started_at=d["started_at"],
        finished_at=d["finished_at"],
    )


register_mapper(ScenarioJobMapper())
