"""Hot query helpers for the API, reading the normalized relational tables.

These are the queries the API needs that don't fit the per-workspace
``get_entity``/``list_entities`` contract (cross-workspace listing, pagination
with counts, latest-trace-per-case, thread assembly). They were Postgres-only
"hot methods" discovered via ``getattr`` in the dual-backend era; now Postgres is
the only backend so they are part of the storage contract and read the
relational columns directly (no JSON extraction).

Each function takes an open psycopg connection and reuses the entity mappers to
rebuild full models, so a row never has to be hand-assembled twice.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from selfevals.schemas.eval_case import EvalCase
from selfevals.schemas.experiment import Experiment
from selfevals.schemas.trace import Trace
from selfevals.storage.postgres.mappers import mapper_for

if TYPE_CHECKING:
    from selfevals.api.schemas import WorkspaceSummary


def _fetchall(conn: Any, sql: str, params: list[Any] | None = None) -> list[tuple[Any, ...]]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [tuple(r) for r in cur.fetchall()]


def list_workspace_summaries(conn: Any) -> list[WorkspaceSummary]:
    from selfevals.api.schemas import WorkspaceSummary
    from selfevals.schemas.workspace import Workspace

    ws_mapper = mapper_for(Workspace)
    rows = _fetchall(
        conn,
        """
        SELECT w.id,
               COUNT(DISTINCT e.id) AS experiment_count,
               MAX(i.updated_at) AS last_run_at
        FROM workspaces w
        LEFT JOIN experiments e ON e.workspace_id = w.id
        LEFT JOIN iteration_records i ON i.workspace_id = w.id
        GROUP BY w.id
        ORDER BY w.created_at DESC
        """,
    )
    out: list[WorkspaceSummary] = []
    with conn.cursor() as cur:
        for ws_id, exp_count, last_run_at in rows:
            ws = ws_mapper.load(cur, ws_id, ws_id)
            if ws is None:
                continue
            out.append(
                WorkspaceSummary(
                    id=ws.id,
                    slug=ws.slug,
                    name=ws.name,
                    description=ws.description,
                    owner_id=ws.owner_id,
                    created_at=ws.created_at,
                    experiment_count=int(exp_count or 0),
                    last_run_at=last_run_at,
                )
            )
    return out


def workspace_by_slug_owner(conn: Any, *, slug: str, user_id: str) -> Any | None:
    from selfevals.schemas.workspace import Workspace

    ws_mapper = mapper_for(Workspace)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM workspaces WHERE slug = %s AND owner_id = %s LIMIT 1",
            (slug, user_id),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return ws_mapper.load(cur, row[0], row[0])


def list_experiments_page(
    conn: Any,
    *,
    workspace_id: str,
    limit: int,
    offset: int,
    state: str | None,
    feature: str | None,
) -> tuple[list[Experiment], int, dict[str, int]]:
    exp_mapper = mapper_for(Experiment)
    clauses = ["workspace_id = %s"]
    params: list[Any] = [workspace_id]
    if state is not None:
        clauses.append("state = %s")
        params.append(state)
    if feature is not None:
        clauses.append("%s = ANY(taxonomy_target_features)")
        params.append(feature)
    where = " AND ".join(clauses)
    total = int(
        _fetchall(conn, f"SELECT COUNT(1) FROM experiments WHERE {where}", params)[0][0]
    )
    id_rows = _fetchall(
        conn,
        f"SELECT id FROM experiments WHERE {where} ORDER BY updated_at DESC LIMIT %s OFFSET %s",
        [*params, limit, offset],
    )
    experiments: list[Experiment] = []
    with conn.cursor() as cur:
        for (exp_id,) in id_rows:
            exp = exp_mapper.load(cur, workspace_id, exp_id)
            if exp is not None:
                experiments.append(exp)
    ids = [e.id for e in experiments]
    counts: dict[str, int] = {}
    if ids:
        for exp_id, count in _fetchall(
            conn,
            """
            SELECT experiment_id, COUNT(1)::int FROM iteration_records
            WHERE workspace_id = %s AND experiment_id = ANY(%s)
            GROUP BY experiment_id
            """,
            [workspace_id, ids],
        ):
            counts[str(exp_id)] = int(count)
    return experiments, total, counts


def eval_cases_for_experiment(
    conn: Any, workspace_id: str, experiment_id: str
) -> list[EvalCase]:
    mapper = mapper_for(EvalCase)
    id_rows = _fetchall(
        conn,
        "SELECT id FROM eval_cases WHERE workspace_id = %s AND experiment_id = %s ORDER BY name ASC",
        [workspace_id, experiment_id],
    )
    out: list[EvalCase] = []
    with conn.cursor() as cur:
        for (cid,) in id_rows:
            ec = mapper.load(cur, workspace_id, cid)
            if ec is not None:
                out.append(ec)
    return out


def latest_trace_refs_by_case(
    conn: Any, workspace_id: str, experiment_id: str
) -> dict[str, tuple[str, str]]:
    rows = _fetchall(
        conn,
        """
        SELECT DISTINCT ON (run_eval_case_id) run_eval_case_id, run_id, id
        FROM traces
        WHERE workspace_id = %s AND run_experiment_id = %s AND run_eval_case_id IS NOT NULL
        ORDER BY run_eval_case_id, run_iteration DESC NULLS LAST, env_started_at DESC
        """,
        [workspace_id, experiment_id],
    )
    return {str(case_id): (str(run_id), str(trace_id)) for case_id, run_id, trace_id in rows}


def traces_for_experiment_iteration(
    conn: Any, workspace_id: str, experiment_id: str, iteration: int
) -> list[Trace]:
    mapper = mapper_for(Trace)
    id_rows = _fetchall(
        conn,
        """
        SELECT id FROM traces
        WHERE workspace_id = %s AND run_experiment_id = %s AND run_iteration = %s
        ORDER BY env_started_at ASC
        """,
        [workspace_id, experiment_id, iteration],
    )
    out: list[Trace] = []
    with conn.cursor() as cur:
        for (tid,) in id_rows:
            tr = mapper.load(cur, workspace_id, tid)
            if tr is not None:
                out.append(tr)
    return out


def trace_by_id_or_run_id(conn: Any, workspace_id: str, trace_id: str) -> Trace | None:
    mapper = mapper_for(Trace)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM traces WHERE workspace_id = %s AND (id = %s OR run_id = %s) LIMIT 1",
            (workspace_id, trace_id, trace_id),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return mapper.load(cur, workspace_id, row[0])


def traces_by_thread_id(conn: Any, workspace_id: str, thread_id: str) -> list[Trace]:
    mapper = mapper_for(Trace)
    id_rows = _fetchall(
        conn,
        """
        SELECT id FROM traces
        WHERE workspace_id = %s AND run_thread_id = %s
        ORDER BY
          CASE WHEN run_thread_position IS NULL THEN 1 ELSE 0 END,
          run_thread_position ASC,
          env_started_at ASC
        """,
        [workspace_id, thread_id],
    )
    out: list[Trace] = []
    with conn.cursor() as cur:
        for (tid,) in id_rows:
            tr = mapper.load(cur, workspace_id, tid)
            if tr is not None:
                out.append(tr)
    return out


def expired_run_job_leases(
    conn: Any, *, now: datetime, limit: int = 100
) -> list[tuple[str, str]]:
    """Cross-workspace listing of run jobs whose lease has lapsed.

    Returns ``(workspace_id, job_id)`` for jobs that are mid-flight
    (``leased``/``running``) but whose ``lease_expires_at`` is in the past — i.e.
    the worker that held them died without writing a terminal state (OOMKill /
    SIGKILL never raise, so the ``execute_run_job`` failure path never runs). The
    sweeper reloads each and routes it through ``mark_run_job_failed`` for the
    retry-vs-dead-letter decision. This is a ``<`` cross-workspace scan, so it
    does not fit the per-workspace ``list_entities`` contract; it rides the
    partial-friendly ``idx_run_jobs_lease`` index.
    """
    return [
        (str(ws_id), str(job_id))
        for ws_id, job_id in _fetchall(
            conn,
            """
            SELECT workspace_id, id FROM run_jobs
            WHERE status IN ('leased', 'running')
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at < %s
            ORDER BY lease_expires_at ASC
            LIMIT %s
            """,
            [now, limit],
        )
    ]


def touch_run_job_lease(
    conn: Any, *, workspace_id: str, job_id: str, owner: str, lease_expires_at: datetime
) -> bool:
    """Renew a job's lease via a direct, unversioned UPDATE (the heartbeat path).

    The lease is operational metadata, not versioned domain state, so it must NOT
    go through ``put_entity``'s version-CAS — the in-flight run holds a stale
    in-memory copy of the row and a CAS would raise ``OptimisticConcurrencyError``.
    The ``status IN ('leased','running')`` guard in the WHERE makes this a no-op
    once the job reaches a terminal state, closing the heartbeat-vs-sweeper race.
    Returns True if a row was renewed.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE run_jobs
            SET lease_owner = %s, lease_expires_at = %s, updated_at = %s
            WHERE id = %s AND workspace_id = %s
              AND status IN ('leased', 'running')
            """,
            (owner, lease_expires_at, lease_expires_at, job_id, workspace_id),
        )
        return bool(cur.rowcount > 0)
