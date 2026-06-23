"""Mapper for IterationRecord — the per-iteration ledger row.

Fixed-shape nested specs (ProposerInputs, ExecutionInfo, IterationMetrics,
IterationDecision) become flat prefixed columns; free-form parameter dicts and
the funnel/confusion breakdowns become JSONB; variable-length lists
(trace_run_ids, guardrails, reliability, failure_mode_counts) become child
tables. ``load`` reassembles the full nested Pydantic model.
"""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from selfevals.schemas.iteration import (
    ExecutionInfo,
    IterationDecision,
    IterationMetrics,
    IterationRecord,
    MetricObservation,
    ProposerInputs,
)
from selfevals.storage.postgres.mappers.base import (
    SHARED_COLUMNS,
    EntityMapper,
    register_mapper,
    shared_values,
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
        e = entity
        m = e.metrics
        d = e.decision
        values = [
            *shared_values(e),
            e.experiment_id,
            e.iteration,
            e.parent_iteration,
            e.state.value,
            e.hypothesis,
            Jsonb(e.proposed_parameters),
            e.duration_seconds,
            e.cost_usd,
            # ProposerInputs
            e.proposer.type.value,
            Jsonb(e.proposer.strategy_parameters),
            list(e.proposer.iterations_consulted),
            list(e.proposer.failure_modes_consulted),
            # ExecutionInfo
            e.execution.variant_id,
            Jsonb(e.execution.ran_against),
            # IterationMetrics
            m is not None,
            m.primary.name if m else None,
            m.primary.value if m else None,
            m.primary.delta_vs_baseline if m else None,
            m.cost_usd if m else None,
            m.duration_seconds if m else None,
            m.error_rate if m else None,
            Jsonb(m.funnel) if m else None,
            Jsonb(m.confusion) if (m and m.confusion is not None) else None,
            # IterationDecision
            d is not None,
            d.outcome.value if d else None,
            d.rationale if d else None,
            d.next_action if d else None,
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
        cur.execute(
            "DELETE FROM iteration_trace_runs WHERE iteration_record_id = %s", (e.id,)
        )
        for pos, trace_run_id in enumerate(e.execution.trace_run_ids):
            cur.execute(
                "INSERT INTO iteration_trace_runs "
                "(iteration_record_id, position, trace_run_id) VALUES (%s, %s, %s)",
                (e.id, pos, trace_run_id),
            )
        cur.execute(
            "DELETE FROM iteration_guardrails WHERE iteration_record_id = %s", (e.id,)
        )
        cur.execute(
            "DELETE FROM iteration_reliability WHERE iteration_record_id = %s", (e.id,)
        )
        cur.execute(
            "DELETE FROM iteration_failure_mode_counts WHERE iteration_record_id = %s",
            (e.id,),
        )
        if m is not None:
            for pos, g in enumerate(m.guardrails):
                cur.execute(
                    "INSERT INTO iteration_guardrails "
                    "(iteration_record_id, position, name, value, delta_vs_baseline) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (e.id, pos, g.name, g.value, g.delta_vs_baseline),
                )
            for metric_name, value in m.reliability.items():
                cur.execute(
                    "INSERT INTO iteration_reliability "
                    "(iteration_record_id, metric_name, value) VALUES (%s, %s, %s)",
                    (e.id, metric_name, value),
                )
            for failure_mode, count in m.failure_mode_counts.items():
                cur.execute(
                    "INSERT INTO iteration_failure_mode_counts "
                    "(iteration_record_id, failure_mode, count) VALUES (%s, %s, %s)",
                    (e.id, failure_mode, count),
                )

    def load(self, cur: Any, workspace_id: str, entity_id: str) -> IterationRecord | None:
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
        return [self._build(cur, row) for row in rows]

    def _build(self, cur: Any, row: tuple[Any, ...]) -> IterationRecord:
        d = dict(zip(_ALL_COLUMNS, row, strict=True))
        rid = d["id"]
        # Child rows.
        cur.execute(
            "SELECT trace_run_id FROM iteration_trace_runs "
            "WHERE iteration_record_id = %s ORDER BY position",
            (rid,),
        )
        trace_run_ids = [r[0] for r in cur.fetchall()]

        metrics: IterationMetrics | None = None
        if d["metrics_present"]:
            cur.execute(
                "SELECT name, value, delta_vs_baseline FROM iteration_guardrails "
                "WHERE iteration_record_id = %s ORDER BY position",
                (rid,),
            )
            guardrails = [
                MetricObservation(name=n, value=v, delta_vs_baseline=dvb)
                for n, v, dvb in cur.fetchall()
            ]
            cur.execute(
                "SELECT metric_name, value FROM iteration_reliability "
                "WHERE iteration_record_id = %s ORDER BY metric_name",
                (rid,),
            )
            reliability = {name: value for name, value in cur.fetchall()}
            cur.execute(
                "SELECT failure_mode, count FROM iteration_failure_mode_counts "
                "WHERE iteration_record_id = %s ORDER BY failure_mode",
                (rid,),
            )
            failure_mode_counts = {fm: count for fm, count in cur.fetchall()}
            metrics = IterationMetrics(
                primary=MetricObservation(
                    name=d["metrics_primary_name"],
                    value=d["metrics_primary_value"],
                    delta_vs_baseline=d["metrics_primary_delta_vs_baseline"],
                ),
                guardrails=guardrails,
                reliability=reliability,
                cost_usd=d["metrics_cost_usd"],
                duration_seconds=d["metrics_duration_seconds"],
                error_rate=d["metrics_error_rate"],
                failure_mode_counts=failure_mode_counts,
                funnel=d["metrics_funnel"] or {},
                confusion=d["metrics_confusion"],
            )

        decision: IterationDecision | None = None
        if d["decision_present"]:
            decision = IterationDecision(
                outcome=d["decision_outcome"],
                rationale=d["decision_rationale"],
                next_action=d["decision_next_action"],
            )

        return IterationRecord(
            id=d["id"],
            workspace_id=d["workspace_id"],
            version=d["version"],
            created_at=d["created_at"],
            updated_at=d["updated_at"],
            deleted_at=d["deleted_at"],
            experiment_id=d["experiment_id"],
            iteration=d["iteration"],
            parent_iteration=d["parent_iteration"],
            state=d["state"],
            proposer=ProposerInputs(
                type=d["proposer_type"],
                strategy_parameters=d["proposer_strategy_parameters"],
                iterations_consulted=d["proposer_iterations_consulted"],
                failure_modes_consulted=d["proposer_failure_modes_consulted"],
            ),
            hypothesis=d["hypothesis"],
            proposed_parameters=d["proposed_parameters"],
            execution=ExecutionInfo(
                variant_id=d["execution_variant_id"],
                ran_against=d["execution_ran_against"],
                trace_run_ids=trace_run_ids,
            ),
            metrics=metrics,
            decision=decision,
            duration_seconds=d["duration_seconds"],
            cost_usd=d["cost_usd"],
        )


register_mapper(IterationRecordMapper())
