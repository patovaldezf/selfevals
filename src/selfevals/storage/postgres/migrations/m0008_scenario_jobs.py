"""m0008 — scenario_jobs + scenario_outcomes (per-case sharding).

The run_job becomes a *coordinator*: instead of running every case in one
process, it seeds one ``scenario_jobs`` row per (iteration, case) and workers
claim them with ``FOR UPDATE SKIP LOCKED``. Each finished scenario writes one
``scenario_outcomes`` row — the durable, relational form of an in-memory
``CaseOutcome`` — so the coordinator aggregates each iteration from storage
instead of holding millions of CaseRuns in memory.

Both tables hang off ``run_jobs`` (ON DELETE CASCADE) and ``experiments``
(DEFERRABLE so a transactional load can insert child-before-parent). The
partial ``idx_scenario_jobs_claimable`` index is what keeps the SKIP LOCKED
claim O(1) across millions of rows: it only indexes the pending frontier.
"""

from __future__ import annotations

from typing import Any

_SQL = """
-- ------------------------------------------------------------ scenario_jobs
CREATE TABLE IF NOT EXISTS scenario_jobs (
    id            TEXT PRIMARY KEY,
    workspace_id  TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    version       INTEGER NOT NULL CHECK (version >= 1),
    created_at    TIMESTAMPTZ NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL,
    deleted_at    TIMESTAMPTZ,

    run_job_id    TEXT NOT NULL
        REFERENCES run_jobs (id) ON DELETE CASCADE
        DEFERRABLE INITIALLY DEFERRED,
    experiment_id TEXT NOT NULL
        REFERENCES experiments (id) ON DELETE CASCADE
        DEFERRABLE INITIALLY DEFERRED,
    iteration     INTEGER NOT NULL CHECK (iteration >= 0),
    case_id       TEXT NOT NULL,
    reps          INTEGER NOT NULL DEFAULT 1 CHECK (reps >= 1),
    status        TEXT NOT NULL DEFAULT 'pending' CHECK (
        status IN ('pending', 'claimed', 'running', 'succeeded',
                   'failed', 'dead_lettered', 'cancelled')
    ),
    attempt       INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    max_attempts  INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts >= 1),
    lease_until   TIMESTAMPTZ,
    worker_id     TEXT,
    error         TEXT,
    parameter_overrides JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_at    TIMESTAMPTZ,
    finished_at   TIMESTAMPTZ,

    UNIQUE (run_job_id, iteration, case_id)
);
-- Partial index over the pending frontier only: the SKIP LOCKED claim scans
-- this, so it stays cheap no matter how many finished rows accumulate.
CREATE INDEX IF NOT EXISTS idx_scenario_jobs_claimable
    ON scenario_jobs (run_job_id, iteration)
    WHERE status = 'pending';
-- Drives the coordinator's barrier COUNT(status) per iteration.
CREATE INDEX IF NOT EXISTS idx_scenario_jobs_barrier
    ON scenario_jobs (run_job_id, iteration, status);
-- Lets the lease sweeper find scenario jobs whose worker died.
CREATE INDEX IF NOT EXISTS idx_scenario_jobs_lease
    ON scenario_jobs (lease_until)
    WHERE status IN ('claimed', 'running');

-- -------------------------------------------------------- scenario_outcomes
-- One row per finished (iteration, case): the relational form of a CaseOutcome.
-- Authoritative for metrics; traces are diagnostic. The coordinator aggregates
-- an iteration by reading these, never from in-memory CaseRuns.
CREATE TABLE IF NOT EXISTS scenario_outcomes (
    id            TEXT PRIMARY KEY,
    workspace_id  TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    version       INTEGER NOT NULL CHECK (version >= 1),
    created_at    TIMESTAMPTZ NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL,
    deleted_at    TIMESTAMPTZ,

    run_job_id    TEXT NOT NULL
        REFERENCES run_jobs (id) ON DELETE CASCADE
        DEFERRABLE INITIALLY DEFERRED,
    scenario_job_id TEXT NOT NULL
        REFERENCES scenario_jobs (id) ON DELETE CASCADE
        DEFERRABLE INITIALLY DEFERRED,
    experiment_id TEXT NOT NULL,
    iteration     INTEGER NOT NULL CHECK (iteration >= 0),
    case_id       TEXT NOT NULL,

    -- CaseOutcome shape: per-rep labels/scores plus rolled-up scalars. Arrays
    -- and per-grader/funnel/confusion structures stay JSONB (free-form by design).
    labels                 JSONB NOT NULL DEFAULT '[]'::jsonb,
    scores                 JSONB NOT NULL DEFAULT '[]'::jsonb,
    per_grader_labels      JSONB NOT NULL DEFAULT '{}'::jsonb,
    failure_modes          JSONB NOT NULL DEFAULT '[]'::jsonb,
    breakdowns             JSONB NOT NULL DEFAULT '[]'::jsonb,
    failure_weights        JSONB NOT NULL DEFAULT '{}'::jsonb,
    critical_failure_modes JSONB NOT NULL DEFAULT '[]'::jsonb,
    cost_usd          DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    duration_ms       INTEGER NOT NULL DEFAULT 0,
    llm_call_count    INTEGER NOT NULL DEFAULT 0,
    cache_hit_count   INTEGER NOT NULL DEFAULT 0,

    UNIQUE (run_job_id, iteration, case_id)
);
CREATE INDEX IF NOT EXISTS idx_scenario_outcomes_iteration
    ON scenario_outcomes (run_job_id, iteration);
"""


def up(cur: Any) -> None:
    cur.execute(_SQL)
