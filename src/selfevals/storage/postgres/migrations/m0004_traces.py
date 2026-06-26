"""m0004 — traces + spans (polymorphic) + grader results + links.

Trace is the heaviest record. Layout:

* ``traces`` — main row with the shared columns plus the fixed-shape nested value
  objects (RunInfo, AgentSnapshotRef, EnvironmentInfo, FinalState, TraceMetrics)
  flattened into prefixed columns. ``outputs.structured_output`` is JSONB.
* ``trace_spans`` — one row per span with the shared ``_SpanBase`` fields and the
  ``kind`` discriminator. Ordered by ``span_index``.
* one child table per span kind holding that kind's specific fields, 1:1 with the
  span row (``span_id`` PK, FK to ``trace_spans`` ON DELETE CASCADE). Token/cost
  breakdowns for LLM calls and the LLM ``output`` live as columns on
  ``trace_llm_calls`` (+ ``trace_llm_tool_requests`` for the requested tool list).
* ``trace_grader_results`` — ordered grader results (failure_modes -> TEXT[];
  breakdown -> JSONB).
* ``trace_links`` — ordered TraceLink list.
* ``trace_retrieved_docs`` — RetrievalSpan.retrieved list.

All span/child/grader/link tables FK to traces (or trace_spans) ON DELETE CASCADE
so deleting a trace cleans up every fact in one statement.
"""

from __future__ import annotations

from typing import Any

_SQL = """
CREATE TABLE IF NOT EXISTS traces (
    id            TEXT PRIMARY KEY,
    workspace_id  TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    version       INTEGER NOT NULL CHECK (version >= 1),
    created_at    TIMESTAMPTZ NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL,
    deleted_at    TIMESTAMPTZ,

    schema_version TEXT NOT NULL,
    snapshot_id    TEXT,

    -- RunInfo
    run_id            TEXT NOT NULL,
    run_experiment_id TEXT,
    run_iteration     INTEGER,
    run_variant_id    TEXT,
    run_eval_case_id  TEXT,
    run_repetition    INTEGER NOT NULL DEFAULT 0,
    run_seed          INTEGER,
    run_thread_id     TEXT,
    run_thread_position INTEGER,

    -- AgentSnapshotRef
    agent_fleet_version          INTEGER,
    agent_agent_id               TEXT NOT NULL,
    agent_agent_version          INTEGER NOT NULL,
    agent_parameters_snapshot_id TEXT,

    -- EnvironmentInfo
    env_framework_version TEXT NOT NULL,
    env_runtime           TEXT NOT NULL,
    env_sandbox           TEXT NOT NULL CHECK (
        env_sandbox IN ('mock', 'dry_run', 'live_sandboxed', 'live_canary')
    ),
    env_tool_mocks        TEXT[] NOT NULL DEFAULT '{}',
    env_started_at        TIMESTAMPTZ NOT NULL,
    env_ended_at          TIMESTAMPTZ,

    -- FinalState
    final_state_status TEXT NOT NULL CHECK (
        final_state_status IN ('completed', 'errored', 'timeout', 'aborted')
    ),
    final_state_error  TEXT,

    -- TraceOutputs
    outputs_final_response_pointer TEXT,
    outputs_structured_output      JSONB,

    -- TraceMetrics
    metrics_total_tokens_in   INTEGER NOT NULL DEFAULT 0,
    metrics_total_tokens_out  INTEGER NOT NULL DEFAULT 0,
    metrics_total_cost_usd    DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    metrics_total_duration_ms INTEGER NOT NULL DEFAULT 0,
    metrics_tool_call_count   INTEGER NOT NULL DEFAULT 0,
    metrics_llm_call_count    INTEGER NOT NULL DEFAULT 0,
    metrics_retries           INTEGER NOT NULL DEFAULT 0,
    metrics_recovery_events   INTEGER NOT NULL DEFAULT 0,
    metrics_loop_detected     BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_traces_workspace_run ON traces (workspace_id, run_id);
CREATE INDEX IF NOT EXISTS idx_traces_workspace_thread
    ON traces (workspace_id, run_thread_id, run_thread_position, env_started_at);
CREATE INDEX IF NOT EXISTS idx_traces_workspace_experiment_iteration
    ON traces (workspace_id, run_experiment_id, run_iteration);
CREATE INDEX IF NOT EXISTS idx_traces_workspace_case_started
    ON traces (workspace_id, run_eval_case_id, env_started_at DESC);

-- Polymorphic span base row.
CREATE TABLE IF NOT EXISTS trace_spans (
    span_id    TEXT NOT NULL,
    trace_id   TEXT NOT NULL REFERENCES traces (id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL,
    span_index INTEGER NOT NULL,
    kind       TEXT NOT NULL CHECK (kind IN (
        'agent_turn', 'llm_call', 'tool_call', 'retrieval', 'memory_read',
        'memory_write', 'decision', 'handoff', 'human_intervention',
        'guardrail_check', 'error', 'custom'
    )),
    parent_id   TEXT,
    name        TEXT NOT NULL,
    started_at  TIMESTAMPTZ NOT NULL,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (trace_id, span_id),
    CONSTRAINT trace_spans_index_unique UNIQUE (trace_id, span_index)
);
CREATE INDEX IF NOT EXISTS idx_trace_spans_trace ON trace_spans (trace_id, span_index);
CREATE INDEX IF NOT EXISTS idx_trace_spans_kind ON trace_spans (workspace_id, kind);

-- LLM call detail (1:1 with an llm_call span). Tokens/cost/output flattened.
CREATE TABLE IF NOT EXISTS trace_llm_calls (
    span_id  TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    PRIMARY KEY (trace_id, span_id),
    FOREIGN KEY (trace_id, span_id) REFERENCES trace_spans (trace_id, span_id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    model    TEXT NOT NULL,
    model_version_pinned TEXT,
    system_prompt_pointer TEXT,
    system_prompt_hash    TEXT,
    system_prompt_inline  TEXT,
    messages_pointer TEXT,
    messages_hash    TEXT,
    messages_inline  TEXT,
    tools_offered      TEXT[] NOT NULL DEFAULT '{}',
    tools_offered_hash TEXT,
    params           JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- ReasoningBlock
    reasoning_available    BOOLEAN NOT NULL DEFAULT FALSE,
    reasoning_redacted     BOOLEAN NOT NULL DEFAULT FALSE,
    reasoning_summary_pointer TEXT,
    reasoning_full_pointer TEXT,
    reasoning_thinking_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_signature    TEXT,
    -- LLMOutput
    output_content_pointer TEXT,
    output_content_hash    TEXT,
    output_content_inline  TEXT,
    output_stop_reason     TEXT,
    -- TokenBreakdown
    tokens_input                INTEGER NOT NULL DEFAULT 0,
    tokens_input_cache_read     INTEGER NOT NULL DEFAULT 0,
    tokens_input_cache_creation INTEGER NOT NULL DEFAULT 0,
    tokens_output               INTEGER NOT NULL DEFAULT 0,
    tokens_reasoning            INTEGER NOT NULL DEFAULT 0,
    tokens_total                INTEGER NOT NULL DEFAULT 0,
    -- CostBreakdown
    cost_input          DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    cost_cache_read     DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    cost_cache_creation DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    cost_output         DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    cost_total          DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    time_to_first_token_ms INTEGER,
    tokens_per_second   DOUBLE PRECISION,
    retries             INTEGER NOT NULL DEFAULT 0,
    cache_hit           BOOLEAN NOT NULL DEFAULT FALSE,
    provider_metadata   JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_trace_llm_calls_model
    ON trace_llm_calls (workspace_id, provider, model);

-- LLMOutput.tool_use_requested (ordered list of {tool, tool_use_id}).
CREATE TABLE IF NOT EXISTS trace_llm_tool_requests (
    trace_id    TEXT NOT NULL,
    span_id     TEXT NOT NULL,
    position    INTEGER NOT NULL,
    tool        TEXT NOT NULL,
    tool_use_id TEXT NOT NULL,
    PRIMARY KEY (trace_id, span_id, position),
    FOREIGN KEY (trace_id, span_id) REFERENCES trace_llm_calls (trace_id, span_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS trace_tool_calls (
    span_id  TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    PRIMARY KEY (trace_id, span_id),
    FOREIGN KEY (trace_id, span_id) REFERENCES trace_spans (trace_id, span_id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL,
    tool_name    TEXT NOT NULL,
    tool_version TEXT,
    tool_use_id  TEXT,
    args_pointer   TEXT,
    args_hash      TEXT,
    result_pointer TEXT,
    result_hash    TEXT,
    status     TEXT NOT NULL DEFAULT 'ok' CHECK (status IN ('ok', 'error', 'timeout')),
    error      TEXT,
    retry_chain TEXT[] NOT NULL DEFAULT '{}',
    sandboxed  BOOLEAN NOT NULL DEFAULT FALSE,
    side_effects JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_trace_tool_calls_tool
    ON trace_tool_calls (workspace_id, tool_name, status);

CREATE TABLE IF NOT EXISTS trace_retrieval_spans (
    trace_id TEXT NOT NULL,
    span_id  TEXT NOT NULL,
    PRIMARY KEY (trace_id, span_id),
    FOREIGN KEY (trace_id, span_id) REFERENCES trace_spans (trace_id, span_id) ON DELETE CASCADE,
    retriever TEXT NOT NULL,
    query_pointer TEXT,
    query_hash    TEXT,
    query_embedding_model TEXT,
    top_k_requested INTEGER NOT NULL,
    top_k_returned  INTEGER NOT NULL DEFAULT 0,
    reranker        TEXT,
    grounding_used  TEXT[] NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS trace_retrieved_docs (
    trace_id  TEXT NOT NULL,
    span_id   TEXT NOT NULL,
    position  INTEGER NOT NULL,
    doc_id    TEXT NOT NULL,
    doc_version TEXT,
    chunk_id  TEXT,
    raw_score DOUBLE PRECISION,
    rerank_score DOUBLE PRECISION,
    PRIMARY KEY (trace_id, span_id, position),
    FOREIGN KEY (trace_id, span_id) REFERENCES trace_retrieval_spans (trace_id, span_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS trace_memory_read_spans (
    trace_id TEXT NOT NULL,
    span_id  TEXT NOT NULL,
    PRIMARY KEY (trace_id, span_id),
    FOREIGN KEY (trace_id, span_id) REFERENCES trace_spans (trace_id, span_id) ON DELETE CASCADE,
    memory_store TEXT NOT NULL,
    keys_requested TEXT[] NOT NULL DEFAULT '{}',
    keys_hit       TEXT[] NOT NULL DEFAULT '{}',
    keys_missed    TEXT[] NOT NULL DEFAULT '{}',
    values_pointer TEXT
);

CREATE TABLE IF NOT EXISTS trace_memory_write_spans (
    trace_id TEXT NOT NULL,
    span_id  TEXT NOT NULL,
    PRIMARY KEY (trace_id, span_id),
    FOREIGN KEY (trace_id, span_id) REFERENCES trace_spans (trace_id, span_id) ON DELETE CASCADE,
    memory_store TEXT NOT NULL,
    keys_written TEXT[] NOT NULL DEFAULT '{}',
    values_pointer TEXT
);

CREATE TABLE IF NOT EXISTS trace_decision_spans (
    trace_id      TEXT NOT NULL,
    span_id       TEXT NOT NULL,
    PRIMARY KEY (trace_id, span_id),
    FOREIGN KEY (trace_id, span_id) REFERENCES trace_spans (trace_id, span_id) ON DELETE CASCADE,
    decision_type TEXT NOT NULL,
    chosen        TEXT NOT NULL,
    alternatives_considered TEXT[] NOT NULL DEFAULT '{}',
    rationale_pointer TEXT,
    confidence    DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS trace_handoff_spans (
    trace_id TEXT NOT NULL,
    span_id  TEXT NOT NULL,
    PRIMARY KEY (trace_id, span_id),
    FOREIGN KEY (trace_id, span_id) REFERENCES trace_spans (trace_id, span_id) ON DELETE CASCADE,
    target  TEXT NOT NULL,
    payload_pointer TEXT
);

CREATE TABLE IF NOT EXISTS trace_human_intervention_spans (
    trace_id TEXT NOT NULL,
    span_id  TEXT NOT NULL,
    PRIMARY KEY (trace_id, span_id),
    FOREIGN KEY (trace_id, span_id) REFERENCES trace_spans (trace_id, span_id) ON DELETE CASCADE,
    actor   TEXT NOT NULL,
    action  TEXT NOT NULL,
    rationale_pointer TEXT
);

CREATE TABLE IF NOT EXISTS trace_guardrail_check_spans (
    trace_id TEXT NOT NULL,
    span_id  TEXT NOT NULL,
    PRIMARY KEY (trace_id, span_id),
    FOREIGN KEY (trace_id, span_id) REFERENCES trace_spans (trace_id, span_id) ON DELETE CASCADE,
    guardrail TEXT NOT NULL,
    passed    BOOLEAN NOT NULL,
    detail_pointer TEXT
);

CREATE TABLE IF NOT EXISTS trace_error_spans (
    trace_id TEXT NOT NULL,
    span_id  TEXT NOT NULL,
    PRIMARY KEY (trace_id, span_id),
    FOREIGN KEY (trace_id, span_id) REFERENCES trace_spans (trace_id, span_id) ON DELETE CASCADE,
    error_type  TEXT NOT NULL,
    message     TEXT NOT NULL,
    recoverable BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS trace_custom_spans (
    trace_id TEXT NOT NULL,
    span_id  TEXT NOT NULL,
    PRIMARY KEY (trace_id, span_id),
    FOREIGN KEY (trace_id, span_id) REFERENCES trace_spans (trace_id, span_id) ON DELETE CASCADE,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- agent_turn spans carry no extra fields beyond _SpanBase, so no child table.

CREATE TABLE IF NOT EXISTS trace_grader_results (
    trace_id     TEXT NOT NULL REFERENCES traces (id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL,
    result_index INTEGER NOT NULL,
    grader       TEXT NOT NULL,
    label        TEXT NOT NULL,
    score        DOUBLE PRECISION,
    reason       TEXT,
    reason_pointer TEXT,
    confidence   DOUBLE PRECISION,
    failure_modes TEXT[] NOT NULL DEFAULT '{}',
    breakdown    JSONB,
    PRIMARY KEY (trace_id, result_index)
);
CREATE INDEX IF NOT EXISTS idx_trace_grader_results_label
    ON trace_grader_results (workspace_id, grader, label);
CREATE INDEX IF NOT EXISTS idx_trace_grader_results_failure_modes
    ON trace_grader_results USING GIN (failure_modes);

CREATE TABLE IF NOT EXISTS trace_links (
    trace_id TEXT NOT NULL REFERENCES traces (id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    kind     TEXT NOT NULL CHECK (kind IN ('paraphrase_variant', 'replay_of', 'spawned_by')),
    target_trace_id TEXT NOT NULL,
    PRIMARY KEY (trace_id, position)
);
"""


def up(cur: Any) -> None:
    cur.execute(_SQL)
