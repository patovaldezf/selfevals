"""Free functions for `TraceMapper.upsert` — row values + grader/link child rows.

Split out of `trace.py` (same free-function-delegate pattern as
`trace_spans_write.py`): pure functions that take `cur`/the entity and write,
with no `self`. Span writes already live in `trace_spans_write.py`; this
module covers the root row and the two remaining child tables
(`trace_grader_results`, `trace_links`).
"""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from selfevals.schemas.trace import Trace


def trace_row_values(shared: list[Any], t: Trace) -> list[Any]:
    """Flatten `t`'s nested specs into the `_ALL_COLUMNS`-ordered value list."""
    run = t.run
    return [
        *shared,
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


def write_trace_facts(cur: Any, t: Trace) -> None:
    """Replace the grader-result/link child rows for `t` (spans handled by caller)."""
    cur.execute("DELETE FROM trace_grader_results WHERE trace_id = %s", (t.id,))
    cur.execute("DELETE FROM trace_links WHERE trace_id = %s", (t.id,))
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
