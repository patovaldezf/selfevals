"""Mapper for Trace — the heaviest record, with polymorphic spans.

The main row flattens RunInfo/AgentSnapshotRef/EnvironmentInfo/FinalState/
TraceOutputs/TraceMetrics into prefixed columns. Spans are written to
``trace_spans`` (shared base + ``kind``) plus one detail table per kind; on read
each span is rebuilt into its concrete subtype. Grader results and links are
ordered child tables.

Span read/write is split into `trace_spans_write.py`/`trace_spans_read.py`
(one function per span kind) — this class stays the single `EntityMapper`
registered for `Trace`, but its body is the root-row/grader/link concerns only.
"""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from selfevals.schemas.trace import (
    AgentSnapshotRef,
    EnvironmentInfo,
    FinalState,
    GraderResult,
    RunInfo,
    Trace,
    TraceLink,
    TraceMetrics,
    TraceOutputs,
)
from selfevals.storage.postgres.mappers.base import (
    SHARED_COLUMNS,
    EntityMapper,
    register_mapper,
    shared_values,
)
from selfevals.storage.postgres.mappers.trace_spans_read import load_spans
from selfevals.storage.postgres.mappers.trace_spans_write import insert_span

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
        t = entity
        run = t.run
        values = [
            *shared_values(t),
            t.schema_version,
            t.snapshot_id,
            run.run_id,
            run.experiment_id,
            run.iteration,
            run.variant_id,
            run.eval_case_id,
            run.repetition,
            run.seed,
            run.thread_id,
            run.thread_position,
            t.agent.fleet_version,
            t.agent.agent_id,
            t.agent.agent_version,
            t.agent.parameters_snapshot_id,
            t.environment.framework_version,
            t.environment.runtime,
            t.environment.sandbox.value,
            list(t.environment.tool_mocks),
            t.environment.started_at,
            t.environment.ended_at,
            t.final_state.status.value,
            t.final_state.error,
            t.outputs.final_response_pointer,
            Jsonb(t.outputs.structured_output) if t.outputs.structured_output is not None else None,
            t.metrics.total_tokens_in,
            t.metrics.total_tokens_out,
            t.metrics.total_cost_usd,
            t.metrics.total_duration_ms,
            t.metrics.tool_call_count,
            t.metrics.llm_call_count,
            t.metrics.retries,
            t.metrics.recovery_events,
            t.metrics.loop_detected,
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
        # Replace all facts (idempotent on update). Span child rows cascade from
        # trace_spans, and grader/link rows cascade from traces — but we delete
        # explicitly so an update with fewer spans/results doesn't leave stragglers.
        cur.execute("DELETE FROM trace_spans WHERE trace_id = %s", (t.id,))
        cur.execute("DELETE FROM trace_grader_results WHERE trace_id = %s", (t.id,))
        cur.execute("DELETE FROM trace_links WHERE trace_id = %s", (t.id,))
        for index, span in enumerate(t.spans):
            insert_span(cur, t.id, t.workspace_id, index, span)
        for index, gr in enumerate(t.grader_results):
            cur.execute(
                """
                INSERT INTO trace_grader_results
                  (trace_id, workspace_id, result_index, grader, label, score,
                   reason, reason_pointer, confidence, failure_modes, breakdown)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    t.id,
                    t.workspace_id,
                    index,
                    gr.grader,
                    gr.label,
                    gr.score,
                    gr.reason,
                    gr.reason_pointer,
                    gr.confidence,
                    list(gr.failure_modes),
                    Jsonb(gr.breakdown) if gr.breakdown is not None else None,
                ),
            )
        for index, link in enumerate(t.links):
            cur.execute(
                "INSERT INTO trace_links (trace_id, position, kind, target_trace_id) "
                "VALUES (%s, %s, %s, %s)",
                (t.id, index, link.kind, link.trace_id),
            )

    def load(self, cur: Any, workspace_id: str, entity_id: str) -> Trace | None:
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
        return [self._build(cur, row) for row in rows]

    def _build(self, cur: Any, row: tuple[Any, ...]) -> Trace:
        d = dict(zip(_ALL_COLUMNS, row, strict=True))
        tid = d["id"]
        spans = load_spans(cur, tid)
        cur.execute(
            """
            SELECT grader, label, score, reason, reason_pointer, confidence,
                   failure_modes, breakdown
            FROM trace_grader_results WHERE trace_id = %s ORDER BY result_index
            """,
            (tid,),
        )
        grader_results = [
            GraderResult(
                grader=g,
                label=lbl,
                score=sc,
                reason=rs,
                reason_pointer=rp,
                confidence=cf,
                failure_modes=fm,
                breakdown=bd,
            )
            for g, lbl, sc, rs, rp, cf, fm, bd in cur.fetchall()
        ]
        cur.execute(
            "SELECT kind, target_trace_id FROM trace_links WHERE trace_id = %s ORDER BY position",
            (tid,),
        )
        links = [TraceLink(kind=k, trace_id=tt) for k, tt in cur.fetchall()]
        return Trace(
            id=d["id"],
            workspace_id=d["workspace_id"],
            version=d["version"],
            created_at=d["created_at"],
            updated_at=d["updated_at"],
            deleted_at=d["deleted_at"],
            schema_version=d["schema_version"],
            snapshot_id=d["snapshot_id"],
            run=RunInfo(
                run_id=d["run_id"],
                experiment_id=d["run_experiment_id"],
                iteration=d["run_iteration"],
                variant_id=d["run_variant_id"],
                eval_case_id=d["run_eval_case_id"],
                repetition=d["run_repetition"],
                seed=d["run_seed"],
                thread_id=d["run_thread_id"],
                thread_position=d["run_thread_position"],
            ),
            agent=AgentSnapshotRef(
                fleet_version=d["agent_fleet_version"],
                agent_id=d["agent_agent_id"],
                agent_version=d["agent_agent_version"],
                parameters_snapshot_id=d["agent_parameters_snapshot_id"],
            ),
            environment=EnvironmentInfo(
                framework_version=d["env_framework_version"],
                runtime=d["env_runtime"],
                sandbox=d["env_sandbox"],
                tool_mocks=d["env_tool_mocks"],
                started_at=d["env_started_at"],
                ended_at=d["env_ended_at"],
            ),
            final_state=FinalState(status=d["final_state_status"], error=d["final_state_error"]),
            spans=spans,
            outputs=TraceOutputs(
                final_response_pointer=d["outputs_final_response_pointer"],
                structured_output=d["outputs_structured_output"],
            ),
            grader_results=grader_results,
            metrics=TraceMetrics(
                total_tokens_in=d["metrics_total_tokens_in"],
                total_tokens_out=d["metrics_total_tokens_out"],
                total_cost_usd=d["metrics_total_cost_usd"],
                total_duration_ms=d["metrics_total_duration_ms"],
                tool_call_count=d["metrics_tool_call_count"],
                llm_call_count=d["metrics_llm_call_count"],
                retries=d["metrics_retries"],
                recovery_events=d["metrics_recovery_events"],
                loop_detected=d["metrics_loop_detected"],
            ),
            links=links,
        )


register_mapper(TraceMapper())
