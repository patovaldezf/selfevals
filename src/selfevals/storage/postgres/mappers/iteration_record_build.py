"""Free function for `IterationRecordMapper._build` — reassembles a row.

Split out of `iteration_record.py` (same free-function-delegate pattern as
`trace_spans_read.py`): a pure function of `cur` + the flattened row dict,
no `self`.
"""

from __future__ import annotations

from typing import Any

from selfevals.schemas.iteration import (
    ExecutionInfo,
    IterationDecision,
    IterationMetrics,
    IterationRecord,
    MetricObservation,
    ProposerInputs,
)


def build_iteration_record(
    cur: Any, row: tuple[Any, ...], all_columns: tuple[str, ...]
) -> IterationRecord:
    d = dict(zip(all_columns, row, strict=True))
    rid = d["id"]
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
