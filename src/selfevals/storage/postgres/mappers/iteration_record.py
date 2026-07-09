"""Mapper for IterationRecord — the per-iteration ledger row.

Fixed-shape nested specs (ProposerInputs, ExecutionInfo, IterationMetrics,
IterationDecision) become flat prefixed columns; free-form parameter dicts and
the funnel/confusion breakdowns become JSONB; variable-length lists
(trace_run_ids, guardrails, reliability, failure_mode_counts) become child
tables. ``load`` reassembles the full nested Pydantic model.

Row-flattening/child-table writes and read-side reassembly are split into
`iteration_record_write.py`/`iteration_record_build.py` (same
free-function-delegate pattern as `trace_spans_write.py`/
`trace_spans_read.py`) — this class stays the single `EntityMapper`
registered for `IterationRecord`, but its body is just the SQL statement +
delegate calls.
"""

from __future__ import annotations

from typing import Any

from selfevals.schemas.iteration import IterationRecord
from selfevals.storage.postgres.mappers.base import (
    SHARED_COLUMNS,
    EntityMapper,
    register_mapper,
    shared_values,
)
from selfevals.storage.postgres.mappers.iteration_record_build import build_iteration_record
from selfevals.storage.postgres.mappers.iteration_record_write import (
    iteration_record_row_values,
    write_iteration_record_children,
)

# Main-table columns after the shared ones, in insert order.
_EXTRA_COLUMNS: tuple[str, ...] = (
    "experiment_id",
    "iteration",
    "parent_iteration",
    "state",
    "hypothesis",
    "proposed_parameters",
    "duration_seconds",
    "cost_usd",
    # ProposerInputs
    "proposer_type",
    "proposer_strategy_parameters",
    "proposer_iterations_consulted",
    "proposer_failure_modes_consulted",
    # ExecutionInfo (trace_run_ids -> child table)
    "execution_variant_id",
    "execution_ran_against",
    # IterationMetrics (nullable)
    "metrics_present",
    "metrics_primary_name",
    "metrics_primary_value",
    "metrics_primary_delta_vs_baseline",
    "metrics_cost_usd",
    "metrics_duration_seconds",
    "metrics_error_rate",
    "metrics_funnel",
    "metrics_confusion",
    # IterationDecision (nullable)
    "decision_present",
    "decision_outcome",
    "decision_rationale",
    "decision_next_action",
)
_ALL_COLUMNS: tuple[str, ...] = (*SHARED_COLUMNS, *_EXTRA_COLUMNS)


class IterationRecordMapper(EntityMapper[IterationRecord]):
    entity_cls = IterationRecord
    table = "iteration_records"
    queryable_columns = frozenset({*SHARED_COLUMNS, "experiment_id", "iteration", "state"})

    def upsert(self, cur: Any, entity: IterationRecord) -> None:
        values = iteration_record_row_values(shared_values(entity), entity)
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
        write_iteration_record_children(cur, entity)

    def load(self, cur: Any, workspace_id: str, entity_id: str) -> IterationRecord | None:
        cur.execute(
            f"SELECT {', '.join(_ALL_COLUMNS)} FROM {self.table} "
            "WHERE id = %s AND workspace_id = %s",
            (entity_id, workspace_id),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return build_iteration_record(cur, row, _ALL_COLUMNS)

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
    ) -> list[IterationRecord]:
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
        return [build_iteration_record(cur, row, _ALL_COLUMNS) for row in rows]


register_mapper(IterationRecordMapper())
