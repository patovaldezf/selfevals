# Changelog

All notable changes to this project will be documented in this file.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [SemVer](https://semver.org/).

## [Unreleased]

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
