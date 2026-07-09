"""Free functions for `IterationRecordMapper.upsert` — row values + child tables.

Split out of `iteration_record.py` (same free-function-delegate pattern as
`trace_spans_write.py`/`trace_spans_read.py`): pure functions that take
`cur`/the entity and write, with no `self`.
"""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from selfevals.schemas.iteration import IterationRecord


def iteration_record_row_values(shared: list[Any], e: IterationRecord) -> list[Any]:
    """Flatten `e`'s nested specs into the `_ALL_COLUMNS`-ordered value list."""
    m = e.metrics
    d = e.decision
    return [
        *shared,
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


def write_iteration_record_children(cur: Any, e: IterationRecord) -> None:
    """Replace the trace-run/guardrail/reliability/failure-mode child rows for `e`."""
    cur.execute("DELETE FROM iteration_trace_runs WHERE iteration_record_id = %s", (e.id,))
    for pos, trace_run_id in enumerate(e.execution.trace_run_ids):
        cur.execute(
            "INSERT INTO iteration_trace_runs "
            "(iteration_record_id, position, trace_run_id) VALUES (%s, %s, %s)",
            (e.id, pos, trace_run_id),
        )
    cur.execute("DELETE FROM iteration_guardrails WHERE iteration_record_id = %s", (e.id,))
    cur.execute("DELETE FROM iteration_reliability WHERE iteration_record_id = %s", (e.id,))
    cur.execute(
        "DELETE FROM iteration_failure_mode_counts WHERE iteration_record_id = %s", (e.id,)
    )
    m = e.metrics
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
