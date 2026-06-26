"""m0002 — experiments + their nested specs.

Experiment is the heaviest config entity. Fixed-shape nested value objects
(TargetSpec.primary, EditableContract, ProposerSpec, RunSpec+ConvergenceSpec,
JudgeDefenses+panel/counterfactuals/human_spot_check, ReliabilitySpec,
DecisionPolicy, ErrorAnalysisSpec+trigger, ExperimentTaxonomy) are flattened
into prefixed columns on the main table. Variable-length lists (guardrails,
the FrozenSnapshot EntityRef lists, taxonomy arrays, reliability/outcome metric
name lists) go to child tables or TEXT[] arrays. Genuinely free-form parameter
spaces (SearchSpace.*, ProposerSpec.parameters) are JSONB.
"""

from __future__ import annotations

from typing import Any

_SQL = """
CREATE TABLE IF NOT EXISTS experiments (
    id           TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    version      INTEGER NOT NULL CHECK (version >= 1),
    created_at   TIMESTAMPTZ NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL,
    deleted_at   TIMESTAMPTZ,

    name          TEXT NOT NULL,
    goal          TEXT NOT NULL,
    mode          TEXT NOT NULL CHECK (mode IN ('agent_loop', 'handoff')),
    state         TEXT NOT NULL DEFAULT 'draft' CHECK (
        state IN ('draft', 'queued', 'running', 'paused', 'completed', 'aborted', 'superseded')
    ),
    content_hash  TEXT,

    -- ExperimentTaxonomy (lists as arrays; all required non-empty enforced by Pydantic)
    taxonomy_target_features TEXT[] NOT NULL,
    taxonomy_target_levels   TEXT[] NOT NULL DEFAULT '{}',
    taxonomy_dataset_types   TEXT[] NOT NULL,

    -- DatasetUsage.optimization (EntityRef -> id + version); gates -> child table
    dataset_optimization_id      TEXT NOT NULL,
    dataset_optimization_version INTEGER,

    -- TargetSpec
    target_primary_name     TEXT NOT NULL,
    target_primary_operator TEXT NOT NULL CHECK (target_primary_operator IN ('>', '>=', '<', '<=', '==')),
    target_primary_value    DOUBLE PRECISION NOT NULL,
    target_primary_grader   TEXT,

    -- EditableContract
    editable_prompt            BOOLEAN NOT NULL DEFAULT TRUE,
    editable_model_params      BOOLEAN NOT NULL DEFAULT TRUE,
    editable_model_choice      BOOLEAN NOT NULL DEFAULT FALSE,
    editable_tool_descriptions BOOLEAN NOT NULL DEFAULT FALSE,
    editable_tool_code         BOOLEAN NOT NULL DEFAULT FALSE,
    editable_workflow_graph    BOOLEAN NOT NULL DEFAULT FALSE,
    editable_skills            BOOLEAN NOT NULL DEFAULT FALSE,
    editable_dataset           BOOLEAN NOT NULL DEFAULT FALSE,
    editable_graders           BOOLEAN NOT NULL DEFAULT FALSE,

    -- SearchSpace (free-form parameter spaces -> JSONB)
    search_space_model_params     JSONB NOT NULL DEFAULT '{}'::jsonb,
    search_space_prompt_variables JSONB NOT NULL DEFAULT '{}'::jsonb,
    search_space_tool_params      JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- FrozenSnapshot scalar refs (lists -> child tables)
    frozen_fleet_id              TEXT NOT NULL,
    frozen_fleet_version         INTEGER,
    frozen_risk_registry_id      TEXT,
    frozen_risk_registry_version INTEGER,
    frozen_feature_registry_id      TEXT,
    frozen_feature_registry_version INTEGER,

    -- ProposerSpec
    proposer_strategy                     TEXT NOT NULL CHECK (
        proposer_strategy IN ('manual', 'grid', 'random', 'bayesian', 'bandit', 'evolutionary', 'llm_proposer')
    ),
    proposer_allow_search_space_expansion BOOLEAN NOT NULL DEFAULT FALSE,
    proposer_parameters                   JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- RunSpec + ConvergenceSpec
    run_sandbox              TEXT NOT NULL CHECK (
        run_sandbox IN ('mock', 'dry_run', 'live_sandboxed', 'live_canary')
    ),
    run_runtime             TEXT NOT NULL DEFAULT 'offline',
    run_sample_strategy     TEXT NOT NULL DEFAULT 'full' CHECK (
        run_sample_strategy IN ('full', 'stratified', 'random_subset')
    ),
    run_max_iterations      INTEGER NOT NULL DEFAULT 20 CHECK (run_max_iterations BETWEEN 1 AND 10000),
    run_repetitions_per_case INTEGER NOT NULL DEFAULT 1 CHECK (run_repetitions_per_case BETWEEN 1 AND 100),
    run_parallelism         INTEGER NOT NULL DEFAULT 8 CHECK (run_parallelism BETWEEN 1 AND 64),
    run_seed                INTEGER,
    run_persist_traces      TEXT NOT NULL DEFAULT 'failed' CHECK (
        run_persist_traces IN ('none', 'all', 'failed')
    ),
    run_convergence_min_delta   DOUBLE PRECISION NOT NULL DEFAULT 0.005,
    run_convergence_patience    INTEGER NOT NULL DEFAULT 3,
    run_convergence_early_stop  BOOLEAN,

    -- JudgeDefenses (+ panel / counterfactuals / human_spot_check / outcome_metrics)
    jd_holdout_visible_to_proposer BOOLEAN NOT NULL DEFAULT FALSE,
    jd_overfit_penalty_max_delta   DOUBLE PRECISION NOT NULL DEFAULT 0.05,
    jd_panel_present               BOOLEAN NOT NULL DEFAULT FALSE,
    jd_panel_members               TEXT[] NOT NULL DEFAULT '{}',
    jd_panel_consensus_rule        TEXT NOT NULL DEFAULT 'majority' CHECK (
        jd_panel_consensus_rule IN ('majority', 'unanimous', 'weighted')
    ),
    jd_cf_enabled                  BOOLEAN NOT NULL DEFAULT FALSE,
    jd_cf_generation_strategy      TEXT NOT NULL DEFAULT 'paraphrase' CHECK (
        jd_cf_generation_strategy IN ('paraphrase', 'manual')
    ),
    jd_cf_pairs_per_case           INTEGER NOT NULL DEFAULT 3,
    jd_cf_max_score_variance       DOUBLE PRECISION NOT NULL DEFAULT 0.05,
    jd_hsc_enabled                 BOOLEAN NOT NULL DEFAULT FALSE,
    jd_hsc_sample_rate             DOUBLE PRECISION NOT NULL DEFAULT 0.05,
    jd_hsc_trigger_on_jump         DOUBLE PRECISION NOT NULL DEFAULT 0.1,
    jd_adversarial_dataset_id      TEXT,
    jd_adversarial_dataset_version INTEGER,
    jd_outcome_metrics_present     BOOLEAN NOT NULL DEFAULT FALSE,
    jd_outcome_metrics             TEXT[] NOT NULL DEFAULT '{}',

    -- ReliabilitySpec
    reliability_repetitions_per_case INTEGER NOT NULL DEFAULT 1,
    reliability_metrics              TEXT[] NOT NULL DEFAULT '{}',

    -- DecisionPolicy
    decision_if_regression_fails    TEXT NOT NULL DEFAULT 'reject' CHECK (
        decision_if_regression_fails IN ('reject', 'investigate', 'spawn_subexperiment')
    ),
    decision_if_guardrail_fails     TEXT NOT NULL DEFAULT 'require_tradeoff_review' CHECK (
        decision_if_guardrail_fails IN ('reject', 'require_tradeoff_review')
    ),
    decision_if_judge_human_disagree TEXT NOT NULL DEFAULT 'escalate_to_calibration' CHECK (
        decision_if_judge_human_disagree IN ('escalate_to_calibration', 'investigate')
    ),

    -- ErrorAnalysisSpec + trigger
    ea_enabled           BOOLEAN NOT NULL DEFAULT FALSE,
    ea_taxonomy          TEXT NOT NULL DEFAULT 'workspace',
    ea_trigger_when      TEXT NOT NULL DEFAULT 'fail_rate_above',
    ea_trigger_threshold DOUBLE PRECISION NOT NULL DEFAULT 0.10,
    ea_scope             TEXT NOT NULL DEFAULT 'failed_only' CHECK (ea_scope IN ('failed_only', 'all'))
);
CREATE INDEX IF NOT EXISTS idx_experiments_workspace_updated
    ON experiments (workspace_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_experiments_workspace_state
    ON experiments (workspace_id, state);
CREATE INDEX IF NOT EXISTS idx_experiments_target_features
    ON experiments USING GIN (taxonomy_target_features);

-- TargetSpec.guardrails (ordered list of MetricTarget)
CREATE TABLE IF NOT EXISTS experiment_guardrails (
    experiment_id TEXT NOT NULL REFERENCES experiments (id) ON DELETE CASCADE,
    position      INTEGER NOT NULL,
    name          TEXT NOT NULL,
    operator      TEXT NOT NULL CHECK (operator IN ('>', '>=', '<', '<=', '==')),
    value         DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (experiment_id, position)
);

-- DatasetUsage.gates (EntityRef list)
CREATE TABLE IF NOT EXISTS experiment_dataset_gates (
    experiment_id TEXT NOT NULL REFERENCES experiments (id) ON DELETE CASCADE,
    position      INTEGER NOT NULL,
    dataset_id    TEXT NOT NULL,
    dataset_version INTEGER,
    PRIMARY KEY (experiment_id, position)
);

-- FrozenSnapshot.agents / tools / datasets / graders (EntityRef lists, kept per kind + ordered)
CREATE TABLE IF NOT EXISTS experiment_frozen_refs (
    experiment_id TEXT NOT NULL REFERENCES experiments (id) ON DELETE CASCADE,
    ref_kind      TEXT NOT NULL CHECK (ref_kind IN ('agent', 'tool', 'dataset', 'grader')),
    position      INTEGER NOT NULL,
    ref_id        TEXT NOT NULL,
    ref_version   INTEGER,
    PRIMARY KEY (experiment_id, ref_kind, position)
);
"""


def up(cur: Any) -> None:
    cur.execute(_SQL)
