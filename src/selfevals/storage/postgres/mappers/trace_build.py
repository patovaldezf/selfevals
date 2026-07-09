"""Free function for `TraceMapper._build` — reassembles a row into `Trace`.

Split out of `trace.py` (same free-function-delegate pattern as
`trace_spans_read.py`): a pure function of `cur` + the flattened row dict, no
`self`. Span reads already live in `trace_spans_read.py`; this module covers
the root row plus the two remaining child tables (`trace_grader_results`,
`trace_links`).
"""

from __future__ import annotations

from typing import Any

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
from selfevals.storage.postgres.mappers.trace_spans_read import load_spans


def _load_trace_facts(cur: Any, tid: str) -> tuple[list[GraderResult], list[TraceLink]]:
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
    return grader_results, links


def build_trace(cur: Any, row: tuple[Any, ...], all_columns: tuple[str, ...]) -> Trace:
    d = dict(zip(all_columns, row, strict=True))
    tid = d["id"]
    spans = load_spans(cur, tid)
    grader_results, links = _load_trace_facts(cur, tid)
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
