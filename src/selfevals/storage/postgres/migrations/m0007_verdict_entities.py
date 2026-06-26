"""m0007 — pairwise verdicts + tournaments (head-to-head judging).

A ``pairwise_verdict`` is an immutable record of an A-vs-B judgement, emitted by
an LLM or a human. Its two sides (``PairRef`` a_ref/b_ref) flatten to ``a_*`` /
``b_*`` columns; one side can be a bare ``content_snapshot`` (text that was never
a persisted entity — the tournament path compares live outputs), so the cross-
entity references (experiment_id / case_id / trace_id / dataset_id) stay as loose
columns with NO FK, exactly like ``traces.run_experiment_id`` and
``hypothesis_records.experiment_id`` (m0006). Existence is validated in the
ingest layer (``runner/pairwise_ops.py``) via ``scope.exists``.

A ``tournament`` is the ranking produced by a batch of pairwise comparisons:
``candidate_ids`` is a TEXT[] and the per-candidate standings (``ranking``)
become child rows in ``tournament_rows`` (real FK + CASCADE to the parent,
patterned on ``experiment_guardrails``).

Also adds ``eval_cases.reference_output`` — the gold/taste answer the pairwise
grader compares the agent's output against (``compare_against: reference``).
"""

from __future__ import annotations

from typing import Any

_SQL = """
-- ---------------------------------------------------------------- pairwise_verdicts
CREATE TABLE IF NOT EXISTS pairwise_verdicts (
    id            TEXT PRIMARY KEY,
    workspace_id  TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    version       INTEGER NOT NULL CHECK (version >= 1),
    created_at    TIMESTAMPTZ NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL,
    deleted_at    TIMESTAMPTZ,

    -- a_ref / b_ref (PairRef flattened; loose refs, no FK — see module docstring)
    a_kind        TEXT NOT NULL CHECK (
        a_kind IN ('agent_output', 'reference', 'iteration', 'arbitrary')
    ),
    a_trace_id        TEXT,
    a_case_id         TEXT,
    a_iteration_id    TEXT,
    a_content_snapshot TEXT,
    b_kind        TEXT NOT NULL CHECK (
        b_kind IN ('agent_output', 'reference', 'iteration', 'arbitrary')
    ),
    b_trace_id        TEXT,
    b_case_id         TEXT,
    b_iteration_id    TEXT,
    b_content_snapshot TEXT,

    preferred     TEXT NOT NULL CHECK (preferred IN ('a', 'b', 'tie')),
    margin        DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    rationale     TEXT,
    judge_kind    TEXT NOT NULL CHECK (judge_kind IN ('llm', 'human')),
    judge_id      TEXT NOT NULL,
    judge_model   TEXT,
    rubric_version INTEGER,
    position      TEXT CHECK (position IN ('ab', 'ba')),
    experiment_id TEXT,  -- loose reference
    case_id       TEXT,  -- loose reference
    dataset_id    TEXT,  -- loose reference
    submitted_at  TIMESTAMPTZ,
    duration_seconds DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS idx_pairwise_verdicts_ws_exp
    ON pairwise_verdicts (workspace_id, experiment_id);

-- ---------------------------------------------------------------- tournaments
CREATE TABLE IF NOT EXISTS tournaments (
    id            TEXT PRIMARY KEY,
    workspace_id  TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    version       INTEGER NOT NULL CHECK (version >= 1),
    created_at    TIMESTAMPTZ NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL,
    deleted_at    TIMESTAMPTZ,

    experiment_id TEXT,  -- loose reference
    strategy      TEXT NOT NULL CHECK (
        strategy IN ('vs_baseline', 'all_pairs', 'sampled', 'swiss')
    ),
    method        TEXT NOT NULL CHECK (method IN ('elo', 'bradley_terry')),
    candidate_ids TEXT[] NOT NULL DEFAULT '{}',
    baseline_id   TEXT,
    n_comparisons INTEGER NOT NULL DEFAULT 0 CHECK (n_comparisons >= 0),
    swap_and_average BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_tournaments_ws_exp
    ON tournaments (workspace_id, experiment_id);

CREATE TABLE IF NOT EXISTS tournament_rows (
    tournament_id TEXT NOT NULL REFERENCES tournaments (id) ON DELETE CASCADE,
    position      INTEGER NOT NULL,
    candidate_id  TEXT NOT NULL,
    rank          INTEGER NOT NULL CHECK (rank >= 1),
    score         DOUBLE PRECISION NOT NULL,
    wins          INTEGER NOT NULL CHECK (wins >= 0),
    losses        INTEGER NOT NULL CHECK (losses >= 0),
    ties          INTEGER NOT NULL CHECK (ties >= 0),
    n_comparisons INTEGER NOT NULL CHECK (n_comparisons >= 0),
    PRIMARY KEY (tournament_id, position)
);

-- ---------------------------------------------------------------- eval_cases.reference_output
ALTER TABLE eval_cases ADD COLUMN IF NOT EXISTS reference_output TEXT;
"""


def up(cur: Any) -> None:
    cur.execute(_SQL)
