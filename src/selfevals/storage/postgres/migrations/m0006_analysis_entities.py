"""m0006 — analysis entities: hypothesis_records, analysis_staging_records.

Both live under selfevals.analysis (not schemas/) and hang off an experiment.
Flat scalar entities; suggested_parameters is the only free-form field (JSONB).
"""

from __future__ import annotations

from typing import Any

_SQL = """
CREATE TABLE IF NOT EXISTS hypothesis_records (
    id            TEXT PRIMARY KEY,
    workspace_id  TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    version       INTEGER NOT NULL CHECK (version >= 1),
    created_at    TIMESTAMPTZ NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL,
    deleted_at    TIMESTAMPTZ,

    -- experiment_id is a loose reference (no FK): analysis artifacts can be
    -- ingested for an experiment whose row isn't present in the same store
    -- (e.g. classifying traces pushed from another run), mirroring how
    -- traces.run_experiment_id stays unconstrained.
    experiment_id        TEXT NOT NULL,
    targets_mode_slug    TEXT NOT NULL,
    statement            TEXT NOT NULL,
    suggested_parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
    consumed_by_iteration INTEGER
);
CREATE INDEX IF NOT EXISTS idx_hypothesis_records_workspace_experiment
    ON hypothesis_records (workspace_id, experiment_id);

CREATE TABLE IF NOT EXISTS analysis_staging_records (
    id            TEXT PRIMARY KEY,
    workspace_id  TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    version       INTEGER NOT NULL CHECK (version >= 1),
    created_at    TIMESTAMPTZ NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL,
    deleted_at    TIMESTAMPTZ,

    experiment_id TEXT NOT NULL,  -- loose reference, see hypothesis_records above
    iteration     INTEGER NOT NULL CHECK (iteration >= 0),
    fail_rate     DOUBLE PRECISION NOT NULL,
    threshold     DOUBLE PRECISION NOT NULL,
    scope         TEXT NOT NULL,
    reason        TEXT NOT NULL,
    consumed      BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_analysis_staging_workspace_experiment
    ON analysis_staging_records (workspace_id, experiment_id);
"""


def up(cur: Any) -> None:
    cur.execute(_SQL)
