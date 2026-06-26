"""m0005 — cold entities: agents, fleets, tools, grader_cards, registries,
failure_modes, annotations.

These are workspace-scoped catalog/config entities with no hard cross-entity
FKs (parent_mode_id / case_id / replacement_feature_id are loose references kept
as plain columns). Fixed-shape nested specs -> prefixed columns or child tables;
free-form dicts (agent/fleet parameters, tool schemas, annotation labels,
feature failure-weight defaults) -> JSONB.
"""

from __future__ import annotations

from typing import Any

_SQL = """
-- ---------------------------------------------------------------- agents
CREATE TABLE IF NOT EXISTS agents (
    id            TEXT PRIMARY KEY,
    workspace_id  TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    version       INTEGER NOT NULL CHECK (version >= 1),
    created_at    TIMESTAMPTZ NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL,
    deleted_at    TIMESTAMPTZ,

    fleet_id      TEXT,
    agent_type    TEXT NOT NULL CHECK (agent_type IN ('system_prompt', 'graph', 'handoff')),
    model_provider TEXT NOT NULL,
    model_name     TEXT NOT NULL,
    system_prompt_pointer   TEXT,
    graph_definition_pointer TEXT,
    handoff_target_id       TEXT,
    tools         TEXT[] NOT NULL DEFAULT '{}',
    features      TEXT[] NOT NULL DEFAULT '{}',
    parameters    JSONB NOT NULL DEFAULT '{}'::jsonb,
    modalities    TEXT[] NOT NULL DEFAULT '{text}',
    content_hash  TEXT,
    status        TEXT NOT NULL DEFAULT 'draft' CHECK (
        status IN ('draft', 'active', 'testing', 'production', 'deprecated')
    )
);
CREATE INDEX IF NOT EXISTS idx_agents_workspace ON agents (workspace_id);

-- ---------------------------------------------------------------- agent_fleets
CREATE TABLE IF NOT EXISTS agent_fleets (
    id            TEXT PRIMARY KEY,
    workspace_id  TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    version       INTEGER NOT NULL CHECK (version >= 1),
    created_at    TIMESTAMPTZ NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL,
    deleted_at    TIMESTAMPTZ,

    name          TEXT NOT NULL,
    description   TEXT,
    features      TEXT[] NOT NULL DEFAULT '{}',
    feature_params JSONB NOT NULL DEFAULT '{}'::jsonb,
    content_hash  TEXT,
    status        TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'active', 'archived'))
);
CREATE INDEX IF NOT EXISTS idx_agent_fleets_workspace ON agent_fleets (workspace_id);

CREATE TABLE IF NOT EXISTS agent_fleet_refs (
    fleet_id  TEXT NOT NULL REFERENCES agent_fleets (id) ON DELETE CASCADE,
    ref_kind  TEXT NOT NULL CHECK (ref_kind IN ('agent', 'tool')),
    position  INTEGER NOT NULL,
    ref_id    TEXT NOT NULL,
    ref_version INTEGER,
    PRIMARY KEY (fleet_id, ref_kind, position)
);

-- ---------------------------------------------------------------- tools
CREATE TABLE IF NOT EXISTS tools (
    id            TEXT PRIMARY KEY,
    workspace_id  TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    version       INTEGER NOT NULL CHECK (version >= 1),
    created_at    TIMESTAMPTZ NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL,
    deleted_at    TIMESTAMPTZ,

    name          TEXT NOT NULL,
    description   TEXT NOT NULL,
    schema_input  JSONB NOT NULL DEFAULT '{}'::jsonb,
    schema_output JSONB,
    code_pointer  TEXT,
    side_effects  BOOLEAN NOT NULL DEFAULT FALSE,
    content_hash  TEXT,
    status        TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'active', 'deprecated'))
);
CREATE INDEX IF NOT EXISTS idx_tools_workspace ON tools (workspace_id);

-- ---------------------------------------------------------------- grader_cards
CREATE TABLE IF NOT EXISTS grader_cards (
    id            TEXT PRIMARY KEY,
    workspace_id  TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    version       INTEGER NOT NULL CHECK (version >= 1),
    created_at    TIMESTAMPTZ NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL,
    deleted_at    TIMESTAMPTZ,

    name          TEXT NOT NULL,
    purpose       TEXT NOT NULL,
    grader_kind   TEXT NOT NULL,
    method        TEXT NOT NULL,
    blocking      BOOLEAN NOT NULL DEFAULT FALSE,
    review_cadence TEXT NOT NULL DEFAULT 'monthly',
    state         TEXT NOT NULL DEFAULT 'calibrating' CHECK (
        state IN ('calibrating', 'calibrated', 'in_use', 'drifting', 'recalibrating', 'retired')
    ),

    -- GraderIO
    io_input_fields     TEXT[] NOT NULL,
    io_output_label_set TEXT[] NOT NULL DEFAULT '{}',
    io_output_kind      TEXT NOT NULL DEFAULT 'label',

    -- HumanReference
    href_dataset_id      TEXT,
    href_annotator_count INTEGER NOT NULL DEFAULT 0,
    href_adjudication    TEXT,

    -- CalibrationMetrics
    metrics_precision  DOUBLE PRECISION,
    metrics_recall     DOUBLE PRECISION,
    metrics_f1         DOUBLE PRECISION,
    metrics_macro_f1   DOUBLE PRECISION,
    metrics_spearman   DOUBLE PRECISION,
    metrics_mae        DOUBLE PRECISION,
    metrics_pairwise_agreement DOUBLE PRECISION,
    metrics_high_risk_false_negatives INTEGER,
    metrics_human_human_agreement DOUBLE PRECISION,

    -- CalibrationThresholds
    thr_min_precision DOUBLE PRECISION,
    thr_min_recall    DOUBLE PRECISION,
    thr_min_f1        DOUBLE PRECISION,
    thr_max_high_risk_false_negatives INTEGER,

    -- DegradeBehavior
    degrade_on_threshold_breach TEXT NOT NULL DEFAULT 'degrade_to_advisory',
    degrade_alert_channels      TEXT[] NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_grader_cards_workspace ON grader_cards (workspace_id);

-- ---------------------------------------------------------------- feature_registries
CREATE TABLE IF NOT EXISTS feature_registries (
    id            TEXT PRIMARY KEY,
    workspace_id  TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    version       INTEGER NOT NULL CHECK (version >= 1),
    created_at    TIMESTAMPTZ NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL,
    deleted_at    TIMESTAMPTZ,

    kind            TEXT NOT NULL CHECK (
        kind IN ('product_feature', 'agent_capability', 'system_capability', 'safety_capability')
    ),
    primary_feature TEXT NOT NULL,
    owner           TEXT,
    description     TEXT NOT NULL,
    failure_weight_defaults JSONB NOT NULL DEFAULT '{}'::jsonb,
    parameters      JSONB,
    status          TEXT NOT NULL DEFAULT 'proposed' CHECK (
        status IN ('proposed', 'active', 'deprecated', 'removed')
    ),
    replacement_feature_id TEXT,

    -- RiskProfile (default_risk)
    risk_overall       TEXT NOT NULL,
    risk_user_trust    TEXT,
    risk_privacy       TEXT,
    risk_reversibility TEXT,
    risk_safety        TEXT,
    risk_cost          TEXT
);
CREATE INDEX IF NOT EXISTS idx_feature_registries_workspace ON feature_registries (workspace_id);

-- ---------------------------------------------------------------- risk_registries
CREATE TABLE IF NOT EXISTS risk_registries (
    id            TEXT PRIMARY KEY,
    workspace_id  TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    version       INTEGER NOT NULL CHECK (version >= 1),
    created_at    TIMESTAMPTZ NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL,
    deleted_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_risk_registries_workspace ON risk_registries (workspace_id);

CREATE TABLE IF NOT EXISTS risk_registry_dimensions (
    risk_registry_id TEXT NOT NULL REFERENCES risk_registries (id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    name     TEXT NOT NULL,
    levels   TEXT[] NOT NULL,
    PRIMARY KEY (risk_registry_id, position)
);

-- ---------------------------------------------------------------- failure_modes
CREATE TABLE IF NOT EXISTS failure_modes (
    id            TEXT PRIMARY KEY,
    workspace_id  TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    version       INTEGER NOT NULL CHECK (version >= 1),
    created_at    TIMESTAMPTZ NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL,
    deleted_at    TIMESTAMPTZ,

    slug          TEXT NOT NULL,
    title         TEXT NOT NULL,
    definition    TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'candidate' CHECK (
        status IN ('candidate', 'official', 'retired')
    ),
    parent_mode_id TEXT,
    proposed_by   TEXT NOT NULL DEFAULT 'seed',
    first_seen_iteration INTEGER,
    superseded_by TEXT,
    CONSTRAINT failure_modes_slug_unique UNIQUE (workspace_id, slug)
);
CREATE INDEX IF NOT EXISTS idx_failure_modes_workspace ON failure_modes (workspace_id);

CREATE TABLE IF NOT EXISTS failure_mode_examples (
    failure_mode_id TEXT NOT NULL REFERENCES failure_modes (id) ON DELETE CASCADE,
    position  INTEGER NOT NULL,
    trace_id  TEXT NOT NULL,
    quote_pointer TEXT,
    quote_hash    TEXT,
    note      TEXT,
    PRIMARY KEY (failure_mode_id, position)
);

-- ---------------------------------------------------------------- annotations
CREATE TABLE IF NOT EXISTS annotations (
    id            TEXT PRIMARY KEY,
    workspace_id  TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    version       INTEGER NOT NULL CHECK (version >= 1),
    created_at    TIMESTAMPTZ NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL,
    deleted_at    TIMESTAMPTZ,

    case_id       TEXT NOT NULL,
    trace_id      TEXT,
    annotator_id  TEXT NOT NULL,
    labels_rubric_version TEXT,
    labels_data   JSONB NOT NULL DEFAULT '{}'::jsonb,
    notes         TEXT,
    confidence    DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    flagged_for_adjudication BOOLEAN NOT NULL DEFAULT FALSE,
    started_at    TIMESTAMPTZ,
    submitted_at  TIMESTAMPTZ,
    duration_seconds DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS idx_annotations_workspace_case ON annotations (workspace_id, case_id);
"""


def up(cur: Any) -> None:
    cur.execute(_SQL)
