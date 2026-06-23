"""Aggregate metrics over the normalized trace tables.

Pass-rate, failure-mode, tool, cost, token and latency rollups, computed in SQL
against the relational trace fact tables (``trace_grader_results``,
``trace_tool_calls``, ``trace_llm_calls``, ``traces``). These replaced the old
in-memory JSON scans entirely — there is one implementation now.
"""

from __future__ import annotations

from typing import Any


def _trace_clauses(
    *,
    workspace_id: str,
    start: Any | None,
    end: Any | None,
    experiment_id: str | None,
) -> tuple[list[str], list[Any]]:
    clauses = ["t.workspace_id = %s"]
    params: list[Any] = [workspace_id]
    if experiment_id is not None:
        clauses.append("t.run_experiment_id = %s")
        params.append(experiment_id)
    if start is not None:
        clauses.append("t.env_started_at >= %s")
        params.append(start)
    if end is not None:
        clauses.append("t.env_started_at <= %s")
        params.append(end)
    return clauses, params


def _fetchall(conn: Any, sql: str, params: list[Any]) -> list[tuple[Any, ...]]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [tuple(r) for r in cur.fetchall()]


def _fetchone(conn: Any, sql: str, params: list[Any]) -> tuple[Any, ...]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    if row is None:
        raise RuntimeError("expected one metrics row")
    return tuple(row)


def pass_rate_metrics(
    conn: Any,
    *,
    workspace_id: str,
    start: Any | None = None,
    end: Any | None = None,
    experiment_id: str | None = None,
    grader: str | None = None,
) -> list[dict[str, Any]]:
    clauses, params = _trace_clauses(
        workspace_id=workspace_id, start=start, end=end, experiment_id=experiment_id
    )
    if grader is not None:
        clauses.append("gr.grader = %s")
        params.append(grader)
    rows = _fetchall(
        conn,
        f"""
        SELECT gr.grader, gr.label, COUNT(1)::int AS count
        FROM trace_grader_results gr
        JOIN traces t ON t.id = gr.trace_id
        WHERE {" AND ".join(clauses)}
        GROUP BY gr.grader, gr.label
        ORDER BY count DESC, gr.grader ASC, gr.label ASC
        """,
        params,
    )
    return [{"grader": str(g), "label": str(lbl), "count": int(c)} for g, lbl, c in rows]


def failure_mode_metrics(
    conn: Any,
    *,
    workspace_id: str,
    start: Any | None = None,
    end: Any | None = None,
    experiment_id: str | None = None,
    grader: str | None = None,
) -> list[dict[str, Any]]:
    clauses, params = _trace_clauses(
        workspace_id=workspace_id, start=start, end=end, experiment_id=experiment_id
    )
    if grader is not None:
        clauses.append("gr.grader = %s")
        params.append(grader)
    rows = _fetchall(
        conn,
        f"""
        SELECT fm AS failure_mode, COUNT(1)::int AS count
        FROM trace_grader_results gr
        JOIN traces t ON t.id = gr.trace_id
        CROSS JOIN LATERAL unnest(gr.failure_modes) AS fm
        WHERE {" AND ".join(clauses)}
        GROUP BY fm
        ORDER BY count DESC, fm ASC
        """,
        params,
    )
    return [{"failure_mode": str(m), "count": int(c)} for m, c in rows]


def tool_metrics(
    conn: Any,
    *,
    workspace_id: str,
    start: Any | None = None,
    end: Any | None = None,
    experiment_id: str | None = None,
    tool_name: str | None = None,
) -> list[dict[str, Any]]:
    clauses, params = _trace_clauses(
        workspace_id=workspace_id, start=start, end=end, experiment_id=experiment_id
    )
    if tool_name is not None:
        clauses.append("tc.tool_name = %s")
        params.append(tool_name)
    rows = _fetchall(
        conn,
        f"""
        SELECT
          tc.tool_name,
          tc.status,
          COUNT(1)::int AS count,
          SUM(CASE WHEN tc.status <> 'ok' OR tc.error IS NOT NULL THEN 1 ELSE 0 END)::int
            AS error_count,
          AVG(sp.duration_ms)::double precision AS avg_duration_ms,
          SUM(COALESCE(array_length(tc.retry_chain, 1), 0))::int AS retry_count
        FROM trace_tool_calls tc
        JOIN trace_spans sp ON sp.span_id = tc.span_id
        JOIN traces t ON t.id = tc.trace_id
        WHERE {" AND ".join(clauses)}
        GROUP BY tc.tool_name, tc.status
        ORDER BY count DESC, tc.tool_name ASC, tc.status ASC
        """,
        params,
    )
    return [
        {
            "tool_name": str(tool),
            "status": str(status),
            "count": int(count),
            "error_count": int(errors or 0),
            "avg_duration_ms": float(avg) if avg is not None else None,
            "retry_count": int(retries or 0),
        }
        for tool, status, count, errors, avg, retries in rows
    ]


def cost_metrics(
    conn: Any,
    *,
    workspace_id: str,
    start: Any | None = None,
    end: Any | None = None,
    experiment_id: str | None = None,
    model: str | None = None,
) -> list[dict[str, Any]]:
    clauses, params = _trace_clauses(
        workspace_id=workspace_id, start=start, end=end, experiment_id=experiment_id
    )
    if model is not None:
        clauses.append("lc.model = %s")
        params.append(model)
    rows = _fetchall(
        conn,
        f"""
        SELECT
          lc.provider,
          lc.model,
          COUNT(1)::int AS call_count,
          SUM(COALESCE(lc.cost_total, 0.0))::double precision AS total_cost_usd,
          AVG(COALESCE(lc.cost_total, 0.0))::double precision AS avg_cost_usd
        FROM trace_llm_calls lc
        JOIN traces t ON t.id = lc.trace_id
        WHERE {" AND ".join(clauses)}
        GROUP BY lc.provider, lc.model
        ORDER BY total_cost_usd DESC, lc.provider ASC, lc.model ASC
        """,
        params,
    )
    return [
        {
            "provider": str(provider),
            "model": str(model_name),
            "call_count": int(count),
            "total_cost_usd": float(total or 0.0),
            "avg_cost_usd": float(avg or 0.0),
        }
        for provider, model_name, count, total, avg in rows
    ]


def token_metrics(
    conn: Any,
    *,
    workspace_id: str,
    start: Any | None = None,
    end: Any | None = None,
    experiment_id: str | None = None,
    model: str | None = None,
) -> list[dict[str, Any]]:
    clauses, params = _trace_clauses(
        workspace_id=workspace_id, start=start, end=end, experiment_id=experiment_id
    )
    if model is not None:
        clauses.append("lc.model = %s")
        params.append(model)
    rows = _fetchall(
        conn,
        f"""
        SELECT
          lc.provider,
          lc.model,
          COUNT(1)::int AS call_count,
          SUM(COALESCE(lc.tokens_input, 0))::bigint AS input_tokens,
          SUM(COALESCE(lc.tokens_output, 0))::bigint AS output_tokens,
          SUM(COALESCE(lc.tokens_reasoning, 0))::bigint AS reasoning_tokens,
          SUM(COALESCE(lc.tokens_total, 0))::bigint AS total_tokens
        FROM trace_llm_calls lc
        JOIN traces t ON t.id = lc.trace_id
        WHERE {" AND ".join(clauses)}
        GROUP BY lc.provider, lc.model
        ORDER BY total_tokens DESC, lc.provider ASC, lc.model ASC
        """,
        params,
    )
    return [
        {
            "provider": str(provider),
            "model": str(model_name),
            "call_count": int(count),
            "input_tokens": int(inp or 0),
            "output_tokens": int(outp or 0),
            "reasoning_tokens": int(reas or 0),
            "total_tokens": int(tot or 0),
        }
        for provider, model_name, count, inp, outp, reas, tot in rows
    ]


def _append_latency(metrics: list[dict[str, Any]], metric: str, row: tuple[Any, ...]) -> None:
    count, p50, p95, p99 = row
    if int(count or 0) <= 0:
        return
    metrics.append(
        {
            "metric": metric,
            "count": int(count),
            "p50_ms": float(p50) if p50 is not None else None,
            "p95_ms": float(p95) if p95 is not None else None,
            "p99_ms": float(p99) if p99 is not None else None,
        }
    )


def latency_metrics(
    conn: Any,
    *,
    workspace_id: str,
    start: Any | None = None,
    end: Any | None = None,
    experiment_id: str | None = None,
) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    clauses, params = _trace_clauses(
        workspace_id=workspace_id, start=start, end=end, experiment_id=experiment_id
    )
    where = " AND ".join(clauses)
    row = _fetchone(
        conn,
        f"""
        SELECT
          COUNT(metrics_total_duration_ms)::int,
          percentile_cont(0.50) WITHIN GROUP (ORDER BY metrics_total_duration_ms)::double precision,
          percentile_cont(0.95) WITHIN GROUP (ORDER BY metrics_total_duration_ms)::double precision,
          percentile_cont(0.99) WITHIN GROUP (ORDER BY metrics_total_duration_ms)::double precision
        FROM traces t
        WHERE {where} AND metrics_total_duration_ms IS NOT NULL
        """,
        params,
    )
    _append_latency(metrics, "trace_duration_ms", row)

    row = _fetchone(
        conn,
        f"""
        SELECT
          COUNT(sp.duration_ms)::int,
          percentile_cont(0.50) WITHIN GROUP (ORDER BY sp.duration_ms)::double precision,
          percentile_cont(0.95) WITHIN GROUP (ORDER BY sp.duration_ms)::double precision,
          percentile_cont(0.99) WITHIN GROUP (ORDER BY sp.duration_ms)::double precision
        FROM trace_tool_calls tc
        JOIN trace_spans sp ON sp.span_id = tc.span_id
        JOIN traces t ON t.id = tc.trace_id
        WHERE {where}
        """,
        params,
    )
    _append_latency(metrics, "tool_duration_ms", row)

    row = _fetchone(
        conn,
        f"""
        SELECT
          COUNT(lc.time_to_first_token_ms)::int,
          percentile_cont(0.50) WITHIN GROUP (ORDER BY lc.time_to_first_token_ms)::double precision,
          percentile_cont(0.95) WITHIN GROUP (ORDER BY lc.time_to_first_token_ms)::double precision,
          percentile_cont(0.99) WITHIN GROUP (ORDER BY lc.time_to_first_token_ms)::double precision
        FROM trace_llm_calls lc
        JOIN traces t ON t.id = lc.trace_id
        WHERE {where} AND lc.time_to_first_token_ms IS NOT NULL
        """,
        params,
    )
    _append_latency(metrics, "ttft_ms", row)
    return metrics
