"""Mapper for Trace — the heaviest record, with polymorphic spans.

The main row flattens RunInfo/AgentSnapshotRef/EnvironmentInfo/FinalState/
TraceOutputs/TraceMetrics into prefixed columns. Spans are written to
``trace_spans`` (shared base + ``kind``) plus one detail table per kind; on read
each span is rebuilt into its concrete subtype. Grader results and links are
ordered child tables.

Span read/write is split into `trace_spans_write.py`/`trace_spans_read.py`
(one function per span kind); the root row + grader/link child tables are
split into `trace_write.py`/`trace_build.py` (same pattern) — this class
stays the single `EntityMapper` registered for `Trace`, but its body is just
the SQL statement + delegate calls.
"""

from __future__ import annotations

from typing import Any

from selfevals.schemas.trace import Trace
from selfevals.storage.postgres.mappers.base import (
    SHARED_COLUMNS,
    EntityMapper,
    register_mapper,
    shared_values,
)
from selfevals.storage.postgres.mappers.trace_build import build_trace
from selfevals.storage.postgres.mappers.trace_spans_write import insert_span
from selfevals.storage.postgres.mappers.trace_write import trace_row_values, write_trace_facts

_EXTRA_COLUMNS: tuple[str, ...] = (
    "schema_version",
    "snapshot_id",
    "run_id",
    "run_experiment_id",
    "run_iteration",
    "run_variant_id",
    "run_eval_case_id",
    "run_repetition",
    "run_seed",
    "run_thread_id",
    "run_thread_position",
    "agent_fleet_version",
    "agent_agent_id",
    "agent_agent_version",
    "agent_parameters_snapshot_id",
    "env_framework_version",
    "env_runtime",
    "env_sandbox",
    "env_tool_mocks",
    "env_started_at",
    "env_ended_at",
    "final_state_status",
    "final_state_error",
    "outputs_final_response_pointer",
    "outputs_structured_output",
    "metrics_total_tokens_in",
    "metrics_total_tokens_out",
    "metrics_total_cost_usd",
    "metrics_total_duration_ms",
    "metrics_tool_call_count",
    "metrics_llm_call_count",
    "metrics_retries",
    "metrics_recovery_events",
    "metrics_loop_detected",
)
_ALL_COLUMNS: tuple[str, ...] = (*SHARED_COLUMNS, *_EXTRA_COLUMNS)


class TraceMapper(EntityMapper[Trace]):
    entity_cls = Trace
    table = "traces"
    queryable_columns = frozenset(
        {*SHARED_COLUMNS, "run_id", "run_experiment_id", "run_iteration", "run_eval_case_id"}
    )
    # Accept the logical nested-path names callers used in the SQLite/JSON era.
    column_aliases = {  # noqa: RUF012 - intentional per-mapper mapping
        "run.experiment_id": "run_experiment_id",
        "run.iteration": "run_iteration",
        "run.run_id": "run_id",
        "run.eval_case_id": "run_eval_case_id",
    }

    def upsert(self, cur: Any, entity: Trace) -> None:
        values = trace_row_values(shared_values(entity), entity)
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
        # Replace all facts (idempotent on update). Span child rows cascade from
        # trace_spans, and grader/link rows cascade from traces — but we delete
        # explicitly so an update with fewer spans/results doesn't leave stragglers.
        cur.execute("DELETE FROM trace_spans WHERE trace_id = %s", (entity.id,))
        for index, span in enumerate(entity.spans):
            insert_span(cur, entity.id, entity.workspace_id, index, span)
        write_trace_facts(cur, entity)

    def load(self, cur: Any, workspace_id: str, entity_id: str) -> Trace | None:
        cur.execute(
            f"SELECT {', '.join(_ALL_COLUMNS)} FROM {self.table} "
            "WHERE id = %s AND workspace_id = %s",
            (entity_id, workspace_id),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return build_trace(cur, row, _ALL_COLUMNS)

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
    ) -> list[Trace]:
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
        return [build_trace(cur, row, _ALL_COLUMNS) for row in rows]


register_mapper(TraceMapper())
