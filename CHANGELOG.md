# Changelog

All notable changes to this project will be documented in this file.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [SemVer](https://semver.org/).

## [Unreleased]

## [0.0.7] - 2026-05-16

### Added — PR 6 + PR 7: OptimizationLoop + Decision matrix

Proposers:
- `Proposer` ABC with `ProposerContext` (iteration index + history).
- `ManualProposer`: walk a caller-supplied list of `Proposal` or
  parameter dicts; raises `SearchSpaceExhaustedError` when done.
- `GridProposer`: cartesian product over list-valued entries in
  `experiment.search_space.model_params`; scalar entries are held
  constant; empty list → raises ValueError.
- `RandomProposer`: independent uniform sampling from each parameter
  spec (list, `{lo, hi}`, `{choices: [...]}`, or scalar constant).
  Bounded by `max_proposals`; seeded for reproducibility.
- All proposals are re-validated against the experiment's editable
  contract before being returned.

Aggregator:
- `aggregate_iteration(case_outcomes, primary_metric, reliability_metrics)`
  computes pass@1 / pass@k / pass^k / consistency_rate /
  stability_score / recovery_rate from per-case `CaseOutcome`s.
- Worst-of policy when multiple graders run on the same repetition:
  ERROR > FAIL > PARTIAL > SKIPPED > PASS.
- Failure-mode counts aggregated by tag.
- Guardrail metrics (`cost_usd_per_case`, `latency_ms_per_case_avg`)
  surfaced when traces report cost/duration.

OptimizationLoop:
- Transitions experiment state DRAFT → QUEUED → RUNNING → COMPLETED.
- For each iteration: ask proposer for a Proposal, run cases through
  the Executor, score per-rep results with the configured graders,
  aggregate, hand to a DecisionEvaluator, persist IterationRecord +
  DecisionRecord (when a WorkspaceScope is provided).
- Terminates on `search_space_exhausted`, `converged`, or
  `max_iterations`. Convergence = no improvement above
  `min_delta` for `patience` consecutive iterations.

Decision matrix (PR 7):
- `evaluate_iteration` (pure) + `DecisionMatrixEvaluator` (object).
  Applies the §10 canónico subset that powers MVP optimization:
  guardrail check → first-iteration target check → improvement vs
  baseline → regression handling per `Experiment.decision` policy
  (reject / investigate / spawn_subexperiment) or guardrail policy
  (reject / require_tradeoff_review).
- Missing guardrail metric values are treated as passing — the runner
  doesn't synthesize every metric in MVP and we don't fail-shut on
  absent data.
- End-to-end integration test wires the evaluator into the loop and
  verifies that improvement / no-improvement / regression each
  produce the right DecisionRecord.outcome.

47 new tests (352 total). mypy strict + ruff clean. Zero new deps.

## [0.0.6] - 2026-05-16

### Added — PR 5: Graders (deterministic + LLM judge + calibration)

- `Grader` ABC with `GraderContext` (case + trace + optional response)
  and `GradeResult` (label / score / reason / confidence / failure_modes
  / details). `GradeLabel` enum: pass, fail, partial, error, skipped.
- `DeterministicGrader`: reads rules off `EvalCase.expected`:
  must_include, must_not_include, required_tools (looks at
  ToolCallSpans in the trace), forbidden_tools, optional regex,
  structured_output equality. Configurable case-sensitive mode. Each
  rule emits a stable failure_mode tag for weighted scoring upstream.
- `LLMJudgeGrader`: invokes any `AgentAdapter` as a judge against a
  rubric prompt (`RubricTemplate` with safe substitution). Parses the
  judge's JSON output into a `JudgeDecision`; unknown labels and bad
  JSON return `GradeLabel.ERROR` rather than crashing. Honors
  `GraderCard.blocking` thresholds: when below calibration the grader
  returns SKIPPED ("degraded to advisory") unless `force=True`.
  Single-judge in MVP; panel infrastructure-ready for post-MVP.
- Calibration helpers (`compute_classification_metrics`): pair
  predictions with human labels by case_id; compute precision, recall,
  F1 for the positive class plus macro-F1, accuracy, per-label
  precision/recall, and confusion matrix. Counts high-risk false
  negatives separately (the failure mode that wakes someone up).
  Class-imbalance guard: undefined precision/recall return None.

25 new tests (305 total). mypy strict + ruff clean. Zero new deps.

## [0.0.5] - 2026-05-16

### Added — PR 4: Runner (agent adapters + sandbox + executor)

- `AgentAdapter` ABC + `AdapterRequest`/`AdapterResponse` dataclasses;
  the narrow contract between bootstrap and the agent under test.
- `EmbeddedAdapter`: wraps a Python callable. Used for tests and
  in-repo agents.
- `CliCommandAdapter`: subprocess + JSON-over-stdio. Configurable
  command, env, timeout.
- `HttpEndpointAdapter`: POST JSON via stdlib `urllib` (no
  third-party HTTP dep). Configurable headers + timeout.
- All three normalize errors into `AdapterError` with the original
  cause preserved.
- `SandboxPolicy`: declarative mock/dry_run rules; `live_sandboxed`
  and `live_canary` are accepted as enum values but `ensure_runnable()`
  blocks them in MVP via `SandboxViolationError`.
- `Executor`: runs an `EvalCase` for N repetitions through a given
  adapter + sandbox; assembles a `Trace` per repetition via
  `TraceRecorder`. Records adapter LLM output as an `LLMCallSpan`,
  each tool use as a `ToolCallSpan` (sandboxed flag per policy),
  and adapter exceptions as `ErrorSpan` + `final_state=errored`.

24 new tests (280 total). mypy strict + ruff clean. Zero new deps.

## [0.0.4] - 2026-05-16

### Added — PR 3: Trace ingestion (recorder + payload router + OTel importer)

- `PayloadRouter` — small payloads (≤4 KB by default) stay inline in
  the Trace JSON; larger ones are written to the `ObjectStoreInterface`
  and replaced with `oss://` pointers + sha256 hashes. Canonical
  JSON encoding for dicts/lists guarantees stable hashing across key
  order.
- `TraceRecorder` — context manager that captures spans during agent
  execution. Span context managers: `agent_turn`, `llm_call`,
  `tool_call`. Convenience emitters: `add_retrieval`,
  `add_memory_read/write`, `add_decision`, `add_handoff`,
  `add_human_intervention`, `add_guardrail_check`, `add_error`.
  Accumulates trace-level metrics (LLM call count, tool call count,
  token totals, retries). Tool call exceptions automatically mark
  the span ERROR with type+message. Exiting the context with an
  uncaught exception marks the trace ERRORED.
- `import_otel_spans` — adapter from a flat list of OTel-style span
  dicts (gen_ai.*, openinference.*) to a bootstrap Trace. Classifies
  spans by `openinference.span.kind` / `gen_ai.*` presence,
  normalizes finish reasons, preserves parent/child links, retains
  unknown attributes in `provider_metadata` or CustomSpan.payload.
  When TOOL spans carry call_ids without explicit linkage, the
  importer synthesizes ToolUseRequest entries on the nearest LLM
  span so the schema invariant holds; if no LLM span exists the
  call_id is dropped silently.
- Public surface: `bootstrap.trace` re-exports `PayloadRouter`,
  `TraceRecorder`, `import_otel_spans`.

26 new tests; 256 total. mypy strict + ruff clean. Zero new deps.

## [0.0.3] - 2026-05-16

### Added — PR 2: Storage layer (SQLite + filesystem + workspace scoping)

- `StorageInterface` / `ObjectStoreInterface` / `WorkspaceScope` ABCs:
  every read or write is bound to one `workspace_id`; cross-tenant
  access is impossible by construction.
- `SQLiteStorage` with single generic `entities` table (entity_type, id,
  workspace_id, version, timestamps, payload JSON) + `objects` table.
  Indexes on (workspace_id, entity_type[, created/updated]) and a
  partial deleted_at index. Optimistic concurrency on `version`.
  WAL journal mode + foreign keys on.
- Homemade migration runner (no alembic dep): forward-only,
  `mNNNN_<slug>.py` modules with `up(conn)`, tracked in
  `_bootstrap_migrations`. Initial migration creates the tables.
- `FilesystemObjectStore`: content-addressed blobs at
  `{root}/{workspace_id}/{prefix2}/{sha256}.bin`; pointer URI
  `oss://{workspace_id}/sha256:...` encodes its workspace.
  SHA256 integrity check on read; collision detected if same hash
  resolves to different bytes.
- `seed_workspace(storage, slug, name, user_id, ...)` helper:
  idempotent by (slug, owner), creates the Workspace + one Member
  per `Role` (viewer, evaluator, experimenter, maintainer, admin,
  auditor) when `assign_all_roles=True`.
- Errors: `EntityNotFoundError`, `WorkspaceMismatchError`,
  `OptimisticConcurrencyError`, `ObjectNotFoundError`,
  `PointerHashMismatchError`, `IntegrityViolationError`.

33 new tests (231 total).

## [0.0.2] - 2026-05-16

### Added — PR 1: Schemas-first scaffolding (Pydantic v2)

Closed enums (`Role`, `Level`, `DatasetSource`, `GroundTruthMethod`,
`DatasetType`, `SandboxMode`, `RuntimeLocation`, `Mode`, `ProposerStrategy`,
`ExperimentState`, `SpanKind`, `StopReason`, `TraceState`,
`ToolCallStatus`, `PIIStatus`, `FeatureKind`/`Status`,
`AgentType`/`Status`, `FleetStatus`, `DatasetStatus`, `ToolStatus`,
`GraderCardState`, `DecisionOutcome`, `IterationState`, `Modality`).

Entities:
- `Workspace`, `Member` — multi-tenant primitives; workspace is
  self-referential (its own workspace_id == id).
- `Tool` — first-class entity needed for `editable.tool_code`.
- `FeatureRegistry`, `RiskRegistry` — declarative taxonomies.
- `AgentFleet`, `Agent` — agent_type-discriminated payloads.
- `EvalCase` — taxonomy (level, feature, source, ground_truth,
  runtime, dataset_type, risk), expected, failure_weights, blocking,
  holdout, PII contract.
- `Dataset` — manifest with split_allocation, lazy statistics by
  manifest_hash, regression-class immutability when frozen.
- `Experiment` — TargetSpec, EditableContract enforcing mode=agent_loop
  for tool_code/workflow_graph/skills, SearchSpace, FrozenSnapshot,
  ProposerSpec (MVP gates non-manual/grid/random), RunSpec, JudgeDefenses
  (live_canary requires outcome_metrics), ReliabilitySpec
  (pass@N/pass^N/consistency_rate/...), DecisionPolicy, state machine.
- `IterationRecord`, `Proposal` (with `validate_against(experiment)` =
  editable contract enforcement), `DecisionRecord` with automated +
  human rationale.
- `GraderCard` with blocking thresholds contract (precision >= 0.90,
  recall >= 0.95, max high-risk FNs == 0).
- `Annotation` with free-form labels + optional rubric_version.
- `Trace` schema (operational §B.2): RunInfo, AgentSnapshotRef,
  EnvironmentInfo, FinalState, discriminated `Span` union (12 kinds),
  TokenBreakdown with cache_read/cache_creation/reasoning, CostBreakdown,
  ReasoningBlock with provider signature, LLMOutput with
  tool_use_requested, ToolCallSpan.tool_use_id linkage validated
  trace-wide.

Internal helpers: ULID + prefixed ULID id generation (stdlib only),
canonical content_hash (sha256), tz-aware UTC time helpers.

Tests: 197 unit tests covering every validator and enum; mypy strict
+ ruff (E/W/F/I/B/UP/N/SIM/RUF) clean.

## [0.0.1] - 2026-05-16

### Added
- Initial repo scaffolding: `pyproject.toml`, ruff + mypy strict + pytest config.
- `docs/spec/` with canonical eval framework spec, operational spec v0.1, taxonomy notes.
