"""m0003 — eval_cases, iteration_records, decision_records, run_jobs, datasets.

These hot entities all hang off experiments (cross-entity FKs are kept
DEFERRABLE INITIALLY DEFERRED so a transactional load can insert child-before-
parent within one commit). Fixed-shape nested value objects become prefixed
columns or 1:many child tables; genuinely free-form dicts (case input/context,
proposed_parameters, funnel/confusion, statistics aggregates, spec_payload) are
JSONB.
"""

from __future__ import annotations

from typing import Any

_SQL = """
-- ---------------------------------------------------------------- eval_cases
CREATE TABLE IF NOT EXISTS eval_cases (
    id            TEXT PRIMARY KEY,
    workspace_id  TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    version       INTEGER NOT NULL CHECK (version >= 1),
    created_at    TIMESTAMPTZ NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL,
    deleted_at    TIMESTAMPTZ,

    experiment_id TEXT
        REFERENCES experiments (id) ON DELETE SET NULL
        DEFERRABLE INITIALLY DEFERRED,
    name          TEXT NOT NULL,
    task_type     TEXT NOT NULL,
    modalities    TEXT[] NOT NULL DEFAULT '{text}',
    input         JSONB NOT NULL,
    context       JSONB,
    graders       TEXT[] NOT NULL DEFAULT '{}',
    failure_weights JSONB NOT NULL DEFAULT '{}'::jsonb,
    holdout       BOOLEAN NOT NULL DEFAULT FALSE,
    content_hash  TEXT,

    -- CaseTaxonomy (feature.secondary + ground_truth.methods -> arrays; risk -> JSONB)
    taxonomy_level            TEXT NOT NULL,
    taxonomy_feature_primary  TEXT NOT NULL,
    taxonomy_feature_secondary TEXT[] NOT NULL DEFAULT '{}',
    taxonomy_source_type      TEXT NOT NULL,
    taxonomy_source_failure_type TEXT,
    taxonomy_source_failure_id   TEXT,
    taxonomy_source_parent_case_id TEXT,
    taxonomy_ground_truth_methods TEXT[] NOT NULL,
    taxonomy_runtime          TEXT NOT NULL DEFAULT 'offline',
    taxonomy_dataset_type     TEXT NOT NULL,
    taxonomy_risk             JSONB,

    -- Expected (scalar + array fields; structured_output/output_schema/aliases -> JSONB)
    expected_outcome          TEXT,
    expected_must_include     TEXT[] NOT NULL DEFAULT '{}',
    expected_min_recall       DOUBLE PRECISION,
    expected_must_not_include TEXT[] NOT NULL DEFAULT '{}',
    expected_required_tools   TEXT[] NOT NULL DEFAULT '{}',
    expected_forbidden_tools  TEXT[] NOT NULL DEFAULT '{}',
    expected_required_citations TEXT[] NOT NULL DEFAULT '{}',
    expected_policy_flags     TEXT[] NOT NULL DEFAULT '{}',
    expected_structured_output JSONB,
    expected_output_schema    JSONB,
    expected_required_sections TEXT[] NOT NULL DEFAULT '{}',
    expected_aliases          JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- Blocking
    blocking_merge   BOOLEAN NOT NULL DEFAULT FALSE,
    blocking_release BOOLEAN NOT NULL DEFAULT FALSE,

    -- CaseMetadata
    metadata_owner          TEXT,
    metadata_tags           TEXT[] NOT NULL DEFAULT '{}',
    metadata_pii_status     TEXT NOT NULL DEFAULT 'raw',
    metadata_approved_raw_by TEXT,
    metadata_approved_raw_at TIMESTAMPTZ,
    metadata_notes          TEXT
);
CREATE INDEX IF NOT EXISTS idx_eval_cases_workspace_experiment
    ON eval_cases (workspace_id, experiment_id);

-- ----------------------------------------------------------- iteration_records
CREATE TABLE IF NOT EXISTS iteration_records (
    id            TEXT PRIMARY KEY,
    workspace_id  TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    version       INTEGER NOT NULL CHECK (version >= 1),
    created_at    TIMESTAMPTZ NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL,
    deleted_at    TIMESTAMPTZ,

    experiment_id TEXT NOT NULL
        REFERENCES experiments (id) ON DELETE CASCADE
        DEFERRABLE INITIALLY DEFERRED,
    iteration         INTEGER NOT NULL CHECK (iteration >= 0),
    parent_iteration  INTEGER CHECK (parent_iteration >= 0),
    state             TEXT NOT NULL CHECK (state IN ('completed', 'failed', 'paused')),
    hypothesis        TEXT NOT NULL,
    proposed_parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
    duration_seconds  DOUBLE PRECISION,
    cost_usd          DOUBLE PRECISION,

    -- ProposerInputs
    proposer_type                TEXT NOT NULL,
    proposer_strategy_parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
    proposer_iterations_consulted INTEGER[] NOT NULL DEFAULT '{}',
    proposer_failure_modes_consulted TEXT[] NOT NULL DEFAULT '{}',

    -- ExecutionInfo (trace_run_ids -> child table)
    execution_variant_id  TEXT NOT NULL,
    execution_ran_against JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- IterationMetrics (nullable; required when state=completed). guardrails/
    -- reliability/failure_mode_counts -> child tables; funnel/confusion -> JSONB.
    metrics_present            BOOLEAN NOT NULL DEFAULT FALSE,
    metrics_primary_name       TEXT,
    metrics_primary_value      DOUBLE PRECISION,
    metrics_primary_delta_vs_baseline DOUBLE PRECISION,
    metrics_cost_usd           DOUBLE PRECISION,
    metrics_duration_seconds   DOUBLE PRECISION,
    metrics_error_rate         DOUBLE PRECISION,
    metrics_funnel             JSONB,
    metrics_confusion          JSONB,

    -- IterationDecision (nullable; required when state=completed)
    decision_present   BOOLEAN NOT NULL DEFAULT FALSE,
    decision_outcome   TEXT,
    decision_rationale TEXT,
    decision_next_action TEXT,

    CONSTRAINT iteration_records_unique UNIQUE (workspace_id, experiment_id, iteration)
);
CREATE INDEX IF NOT EXISTS idx_iterations_workspace_experiment_iteration
    ON iteration_records (workspace_id, experiment_id, iteration);
CREATE INDEX IF NOT EXISTS idx_iterations_workspace_updated
    ON iteration_records (workspace_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS iteration_trace_runs (
    iteration_record_id TEXT NOT NULL
        REFERENCES iteration_records (id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    trace_run_id TEXT NOT NULL,
    PRIMARY KEY (iteration_record_id, position)
);

CREATE TABLE IF NOT EXISTS iteration_guardrails (
    iteration_record_id TEXT NOT NULL
        REFERENCES iteration_records (id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    name     TEXT NOT NULL,
    value    DOUBLE PRECISION NOT NULL,
    delta_vs_baseline DOUBLE PRECISION,
    PRIMARY KEY (iteration_record_id, position)
);

CREATE TABLE IF NOT EXISTS iteration_reliability (
    iteration_record_id TEXT NOT NULL
        REFERENCES iteration_records (id) ON DELETE CASCADE,
    metric_name TEXT NOT NULL,
    value       DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (iteration_record_id, metric_name)
);

CREATE TABLE IF NOT EXISTS iteration_failure_mode_counts (
    iteration_record_id TEXT NOT NULL
        REFERENCES iteration_records (id) ON DELETE CASCADE,
    failure_mode TEXT NOT NULL,
    count        INTEGER NOT NULL CHECK (count >= 0),
    PRIMARY KEY (iteration_record_id, failure_mode)
);

-- ----------------------------------------------------------- decision_records
CREATE TABLE IF NOT EXISTS decision_records (
    id            TEXT PRIMARY KEY,
    workspace_id  TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    version       INTEGER NOT NULL CHECK (version >= 1),
    created_at    TIMESTAMPTZ NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL,
    deleted_at    TIMESTAMPTZ,

    experiment_id TEXT NOT NULL
        REFERENCES experiments (id) ON DELETE CASCADE
        DEFERRABLE INITIALLY DEFERRED,
    iteration     INTEGER NOT NULL CHECK (iteration >= 0),
    variant_id    TEXT NOT NULL,
    outcome       TEXT NOT NULL,
    metrics_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    affected_artifacts TEXT[] NOT NULL DEFAULT '{}',
    superseded_by TEXT,

    -- DecisionRationale (automated + optional human)
    rationale_automated TEXT NOT NULL,
    human_decided_by    TEXT,
    human_decided_at    TIMESTAMPTZ,
    human_notes         TEXT,
    human_overrides_automated BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_decisions_workspace_experiment_iteration
    ON decision_records (workspace_id, experiment_id, iteration);

CREATE TABLE IF NOT EXISTS decision_next_actions (
    decision_record_id TEXT NOT NULL
        REFERENCES decision_records (id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    kind     TEXT NOT NULL,
    payload  JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (decision_record_id, position)
);

-- ---------------------------------------------------------------- run_jobs
CREATE TABLE IF NOT EXISTS run_jobs (
    id            TEXT PRIMARY KEY,
    workspace_id  TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    version       INTEGER NOT NULL CHECK (version >= 1),
    created_at    TIMESTAMPTZ NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL,
    deleted_at    TIMESTAMPTZ,

    experiment_id TEXT NOT NULL
        REFERENCES experiments (id) ON DELETE CASCADE
        DEFERRABLE INITIALLY DEFERRED,
    status        TEXT NOT NULL DEFAULT 'queued' CHECK (
        status IN ('queued', 'leased', 'running', 'succeeded', 'failed', 'cancelled', 'dead_lettered')
    ),
    attempt       INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    max_attempts  INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts >= 1),
    lease_owner   TEXT,
    lease_expires_at TIMESTAMPTZ,
    cancel_requested_at TIMESTAMPTZ,
    started_at    TIMESTAMPTZ,
    finished_at   TIMESTAMPTZ,
    last_error    TEXT,
    spec_payload  JSONB NOT NULL,
    reps          INTEGER NOT NULL DEFAULT 1 CHECK (reps >= 1)
);
CREATE INDEX IF NOT EXISTS idx_run_jobs_workspace_status_updated
    ON run_jobs (workspace_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_run_jobs_lease ON run_jobs (lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_run_jobs_workspace_experiment
    ON run_jobs (workspace_id, experiment_id);

-- ---------------------------------------------------------------- datasets
CREATE TABLE IF NOT EXISTS datasets (
    id            TEXT PRIMARY KEY,
    workspace_id  TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    version       INTEGER NOT NULL CHECK (version >= 1),
    created_at    TIMESTAMPTZ NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL,
    deleted_at    TIMESTAMPTZ,

    name          TEXT NOT NULL,
    description   TEXT,
    dataset_type  TEXT NOT NULL,
    source_dataset_id TEXT,
    manifest_hash TEXT,
    status        TEXT NOT NULL DEFAULT 'draft' CHECK (
        status IN ('draft', 'frozen', 'active', 'archived')
    ),

    -- SplitAllocation (other -> JSONB)
    split_optimization DOUBLE PRECISION NOT NULL DEFAULT 0.7,
    split_holdout      DOUBLE PRECISION NOT NULL DEFAULT 0.2,
    split_reliability  DOUBLE PRECISION NOT NULL DEFAULT 0.1,
    split_other        JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- DatasetStatistics (nullable; aggregates are JSONB by design)
    stats_present       BOOLEAN NOT NULL DEFAULT FALSE,
    stats_total_cases   INTEGER,
    stats_by_level      JSONB,
    stats_by_feature    JSONB,
    stats_by_source     JSONB,
    stats_by_risk       JSONB,
    stats_holdout_count INTEGER,
    stats_pii_breakdown JSONB
);
CREATE INDEX IF NOT EXISTS idx_datasets_workspace ON datasets (workspace_id);

CREATE TABLE IF NOT EXISTS dataset_cases (
    dataset_id   TEXT NOT NULL REFERENCES datasets (id) ON DELETE CASCADE,
    position     INTEGER NOT NULL,
    case_id      TEXT NOT NULL,
    case_version INTEGER,
    PRIMARY KEY (dataset_id, position),
    CONSTRAINT dataset_cases_unique UNIQUE (dataset_id, case_id)
);

-- ------------------------------------------------------------ dataset_baselines
CREATE TABLE IF NOT EXISTS dataset_baselines (
    id            TEXT PRIMARY KEY,
    workspace_id  TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    version       INTEGER NOT NULL CHECK (version >= 1),
    created_at    TIMESTAMPTZ NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL,
    deleted_at    TIMESTAMPTZ,

    dataset_id     TEXT NOT NULL,
    iteration_id   TEXT NOT NULL,
    experiment_id  TEXT NOT NULL,
    primary_metric TEXT NOT NULL,
    primary_value  DOUBLE PRECISION NOT NULL,
    error_rate     DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    confusion      JSONB
);
CREATE INDEX IF NOT EXISTS idx_dataset_baselines_workspace_dataset
    ON dataset_baselines (workspace_id, dataset_id);
"""


def up(cur: Any) -> None:
    cur.execute(_SQL)
