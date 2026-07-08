"""m0009 — arenas, arena_variants, arena_rounds.

Arena is the code-variant competition entity: an `Arena` groups N named
`ArenaVariant`s (each pinned to a resolved git SHA + worktree path), and
`ArenaRound`s that record which child `Experiment`/`RunJob` ran each variant
for that round. Child experiments/run_jobs are NOT foreign-keyed here — they
live in the existing `experiments`/`run_jobs` tables untouched; the round's
`entries` JSONB just records their ids.
"""

from __future__ import annotations

from typing import Any

_SQL = """
CREATE TABLE IF NOT EXISTS arenas (
    id               TEXT PRIMARY KEY,
    workspace_id     TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    version          INTEGER NOT NULL CHECK (version >= 1),
    created_at       TIMESTAMPTZ NOT NULL,
    updated_at       TIMESTAMPTZ NOT NULL,
    deleted_at       TIMESTAMPTZ,

    name             TEXT NOT NULL,
    goal             TEXT NOT NULL,
    repo_path        TEXT NOT NULL,
    agent_command    JSONB NOT NULL,
    agent_env        JSONB,
    spec_template    JSONB NOT NULL,
    dataset_id       TEXT,
    objective_metric TEXT NOT NULL,
    budget           JSONB NOT NULL DEFAULT '{}'::jsonb,
    current_round    INTEGER NOT NULL DEFAULT 0 CHECK (current_round >= 0),
    state            TEXT NOT NULL DEFAULT 'draft' CHECK (
        state IN ('draft', 'active', 'completed', 'archived')
    ),
    winner_variant_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_arenas_workspace_state
    ON arenas (workspace_id, state);

CREATE TABLE IF NOT EXISTS arena_variants (
    id               TEXT PRIMARY KEY,
    workspace_id     TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    version          INTEGER NOT NULL CHECK (version >= 1),
    created_at       TIMESTAMPTZ NOT NULL,
    updated_at       TIMESTAMPTZ NOT NULL,
    deleted_at       TIMESTAMPTZ,

    arena_id         TEXT NOT NULL REFERENCES arenas (id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    git_ref          TEXT NOT NULL,
    resolved_sha     TEXT,
    worktree_path    TEXT,
    setup_command    JSONB,
    env_overrides    JSONB,
    hypothesis       TEXT,
    created_in_round INTEGER NOT NULL DEFAULT 0 CHECK (created_in_round >= 0),
    state            TEXT NOT NULL DEFAULT 'registered' CHECK (
        state IN ('registered', 'preparing', 'ready', 'failed', 'retired')
    ),
    error            TEXT
);
CREATE INDEX IF NOT EXISTS idx_arena_variants_arena
    ON arena_variants (arena_id, state);

CREATE TABLE IF NOT EXISTS arena_rounds (
    id           TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    version      INTEGER NOT NULL CHECK (version >= 1),
    created_at   TIMESTAMPTZ NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL,
    deleted_at   TIMESTAMPTZ,

    arena_id     TEXT NOT NULL REFERENCES arenas (id) ON DELETE CASCADE,
    index_       INTEGER NOT NULL CHECK (index_ >= 0),
    entries      JSONB NOT NULL DEFAULT '[]'::jsonb,
    reps         INTEGER NOT NULL DEFAULT 1 CHECK (reps >= 1),
    state        TEXT NOT NULL DEFAULT 'pending' CHECK (
        state IN ('pending', 'running', 'completed', 'failed', 'cancelled')
    ),
    started_at   TIMESTAMPTZ,
    finished_at  TIMESTAMPTZ,
    summary      JSONB,

    UNIQUE (arena_id, index_)
);
CREATE INDEX IF NOT EXISTS idx_arena_rounds_arena
    ON arena_rounds (arena_id, index_);
"""


def up(cur: Any) -> None:
    cur.execute(_SQL)
