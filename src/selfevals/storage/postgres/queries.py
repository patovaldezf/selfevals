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

from psycopg.types.json import Jsonb

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


# -- scenario jobs: atomic claim / plan / barrier ---------------------------
#
# These bypass the per-workspace get/put contract on purpose. The claim is a
# `SELECT ... FOR UPDATE SKIP LOCKED` + `UPDATE` that MUST run in one explicit
# transaction (the storage's `transaction()` ctx manager wraps it). put_entity's
# version-CAS takes no row lock, so it cannot give us claim atomicity; the row
# lock + SKIP LOCKED is what lets N workers drain a frontier without ever
# double-claiming a row.


def claim_scenario_jobs(
    conn: Any,
    *,
    run_job_id: str,
    iteration: int,
    worker_id: str,
    lease_until: datetime,
    batch: int,
) -> list[Any]:
    """Atomically claim up to ``batch`` pending scenario jobs for one iteration.

    Caller MUST hold an open transaction (see ``PostgresStorage.claim_scenario_jobs``).
    Locks the pending frontier with ``FOR UPDATE SKIP LOCKED`` so concurrent
    workers take disjoint rows, flips them to ``claimed``, bumps ``attempt``, and
    returns the rebuilt entities. Empty list when nothing is claimable.
    """
    # The mapper owns the column tuple + row rebuild; import them to keep one source.
    from selfevals.storage.postgres.mappers.scenario_job import (
        _SCENARIO_COLUMNS,
        _row_to_scenario_job,
    )

    cols = ", ".join(_SCENARIO_COLUMNS)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE scenario_jobs SET
                status = 'claimed',
                worker_id = %s,
                lease_until = %s,
                attempt = attempt + 1,
                updated_at = %s
            WHERE id IN (
                SELECT id FROM scenario_jobs
                WHERE run_job_id = %s AND iteration = %s AND status = 'pending'
                ORDER BY id
                FOR UPDATE SKIP LOCKED
                LIMIT %s
            )
            RETURNING {cols}
            """,
            (worker_id, lease_until, lease_until, run_job_id, iteration, batch),
        )
        return [_row_to_scenario_job(tuple(row)) for row in cur.fetchall()]


def insert_scenario_jobs(conn: Any, jobs: list[Any]) -> int:
    """Batch-insert scenario jobs, idempotent on ``(run_job_id, iteration, case_id)``.

    Re-planning the same iteration (e.g. a coordinator restarted by the sweeper)
    must not duplicate work, so a conflict on the UNIQUE tuple is a no-op. Returns
    how many rows were newly inserted.
    """
    if not jobs:
        return 0
    from selfevals.storage.postgres.mappers.base import shared_values
    from selfevals.storage.postgres.mappers.scenario_job import _SCENARIO_COLUMNS

    cols = ", ".join(_SCENARIO_COLUMNS)
    placeholders = "(" + ", ".join(["%s"] * len(_SCENARIO_COLUMNS)) + ")"
    rows: list[Any] = []
    params: list[Any] = []
    for e in jobs:
        params.extend(
            [
                *shared_values(e),
                e.run_job_id,
                e.experiment_id,
                e.iteration,
                e.case_id,
                e.reps,
                e.status.value,
                e.attempt,
                e.max_attempts,
                e.lease_until,
                e.worker_id,
                e.error,
                Jsonb(e.parameter_overrides),
                e.started_at,
                e.finished_at,
            ]
        )
        rows.append(placeholders)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO scenario_jobs ({cols})
            VALUES {", ".join(rows)}
            ON CONFLICT (run_job_id, iteration, case_id) DO NOTHING
            """,
            params,
        )
        return int(cur.rowcount)


def barrier_counts(conn: Any, *, run_job_id: str, iteration: int) -> dict[str, int]:
    """Count scenario jobs by status for one iteration (the coordinator barrier).

    Rides ``idx_scenario_jobs_barrier``. The coordinator polls this until
    ``pending + claimed + running == 0`` — i.e. every case reached a terminal
    state — before aggregating the iteration.
    """
    rows = _fetchall(
        conn,
        """
        SELECT status, COUNT(1)::int FROM scenario_jobs
        WHERE run_job_id = %s AND iteration = %s
        GROUP BY status
        """,
        [run_job_id, iteration],
    )
    return {str(status): int(count) for status, count in rows}


def finalize_scenario_job(
    conn: Any,
    *,
    job_id: str,
    status: str,
    error: str | None,
    finished_at: datetime,
) -> None:
    """Write a scenario job's terminal (or retry) state via direct SQL.

    Like the run-job heartbeat, this skips put_entity's version-CAS: the worker's
    in-memory copy is stale by design and a CAS would spuriously conflict. The
    status is computed by ``ScenarioJob.mark_*`` on the worker's copy; here we
    only persist the columns.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE scenario_jobs
            SET status = %s, error = %s, worker_id = NULL, lease_until = NULL,
                finished_at = CASE WHEN %s = 'pending' THEN finished_at ELSE %s END,
                updated_at = %s
            WHERE id = %s
            """,
            (status, error, status, finished_at, finished_at, job_id),
        )


def touch_scenario_job_lease(
    conn: Any, *, job_id: str, worker_id: str, lease_until: datetime
) -> bool:
    """Heartbeat a claimed/running scenario job's lease (direct, unversioned)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE scenario_jobs
            SET lease_until = %s, worker_id = %s, updated_at = %s
            WHERE id = %s AND status IN ('claimed', 'running')
            """,
            (lease_until, worker_id, lease_until, job_id),
        )
        return bool(cur.rowcount > 0)


def expired_scenario_job_leases(
    conn: Any, *, now: datetime, limit: int = 100
) -> list[tuple[str, str]]:
    """Cross-run ``(workspace_id, scenario_job_id)`` whose worker died mid-case."""
    return [
        (str(ws_id), str(sid))
        for ws_id, sid in _fetchall(
            conn,
            """
            SELECT workspace_id, id FROM scenario_jobs
            WHERE status IN ('claimed', 'running')
              AND lease_until IS NOT NULL
              AND lease_until < %s
            ORDER BY lease_until ASC
            LIMIT %s
            """,
            [now, limit],
        )
    ]
