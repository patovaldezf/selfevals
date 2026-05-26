# Changelog

All notable changes to this project will be documented in this file.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [SemVer](https://semver.org/).

## [Unreleased]

### Added

- **Thread grouping** — traces can now be assembled into the conversation
  (hilo) they belong to. `RunInfo` gains `thread_id` + `thread_position`; the
  OTel importer auto-detects the thread from `session.id` (OpenInference) or
  `gen_ai.conversation.id` (OTel GenAI), without overwriting an explicit
  caller-set `thread_id`. New read query `load_thread` + `GET
  /workspaces/{ws}/threads/{thread_id}` return every trace sharing a thread,
  ordered by `thread_position` (falling back to `started_at`), each turn
  projected with its grader results so the per-turn calificación is visible.
  `TraceResponse` now surfaces `thread_id` / `thread_position`. This closes the
  last trace-grouping gap versus LangSmith sessions; the run→experiment→
  iteration→decision→grade chain already existed. Eight new tests.
- OTel importer now extracts prompt/completion **message content** into
  traces. `_build_llm_span` reconstructs ordered message lists from both
  attribute families — OpenInference native (`llm.input_messages.{i}.message.*`,
  `llm.output_messages.{i}.message.*`) and the OTel GenAI alias
  (`gen_ai.prompt.{i}.*`, `gen_ai.completion.{i}.*`). When both are present the
  native family wins. Each side gets a stable `content_hash` (on
  `messages_hash` / `output.content_hash`) for dedup and drift detection, and
  the structured messages are kept inline under `provider_metadata`
  (`bootstrap.messages_in` / `bootstrap.messages_out`). Closes the last gap
  versus LangSmith trace capture: the actual prompt and response text are now
  in the trace, not just tokens/model/stop_reason. Five new importer tests.

## [0.1.0] - 2026-05-25

First version where the README no longer lies. `bootstrap run` works
end-to-end against a real LLM agent, error paths are actionable, and
the markdown/JSON reports answer the obvious follow-up questions.
Schema-wise compatible with `0.0.9`.

### Added — usable v1 surface

Examples and quickstart:
- `examples/hello_llm/` — a real Anthropic agent (with deterministic
  fakes when `ANTHROPIC_API_KEY` is unset) over 3 EvalCases:
  sentiment classification, structured extraction, open-ended support
  reply. Two graders combined: `DeterministicGrader` for the rule
  cases + `LLMJudgeGrader` for the open-ended one. `GridProposer`
  sweeps `temperature ∈ {0.0, 0.5, 1.0}`.
- README quickstart points at `evals/experiments/example_pingpong.yaml`
  with the exact commands. Status banner updated from "no runtime
  yet" to "runtime functional".

CLI UX (Day 2):
- Every subcommand (`init`, `workspace`, `experiment`, `iteration`,
  `report`, `run`, `compare`, `estimate`) now has a user-facing
  one-line description and a copy-paste `Example:` epilog. Helper
  `src/bootstrap/cli/_help.py` centralizes the pattern.
- `tests/cli/test_help_texts.py` enforces the contract.
- `docs/adapters.md` documents the three adapters with YAML config,
  per-adapter agent code, contracts, limitations, and a comparison
  table.

Errors and hardening (Day 3):
- `BootstrapError` / `BootstrapUserError` hierarchy. User-correctable
  failures exit with code 2 and a clean one-line message; internal
  errors keep their traceback.
- `src/bootstrap/cli/_friendly.py` is the single translation
  chokepoint for YAML parse errors, dataset paths (with fuzzy-match
  suggestions via stdlib `difflib`), missing graders, HTTP adapter
  transport errors (URL + actionable suffix), and SQLite locked /
  corrupted cases.
- `src/bootstrap/graders/registry.py` — name→factory registry.
  `deterministic` is pre-registered; `llm_judge` is registered
  on-demand by the CLI. YAML can declare top-level `graders:` and
  per-case `EvalCase.graders` filters which graders run.
- `tests/integration/test_full_loop_with_mocked_judge.py` — 7 tests
  covering the happy path plus each of the five friendly-error
  shapes.
- `docs/troubleshooting.md` documents the five common errors and
  fixes.

Reporter (Day 4):
- `src/bootstrap/reporter/_metrics.py` — pure helpers
  (`compute_total_cost`, `compute_total_time_seconds`, etc.) that
  return `None` when data is absent instead of misleading zeros.
- Markdown report gains a "Cost & Time" section (omitted gracefully
  when there are no LLM calls) and a "Next steps" block with
  copy-paste inspection commands.
- JSON report exposes a stable `cost_time` block (`None` when
  missing).
- `src/bootstrap/reporter/compare.py` powers `bootstrap compare`:
  proposal diff table, metrics diff table, failure-mode diff, and a
  "B is better: primary +X; no new failure modes" recommendation.

### Fixed

- Console script `bootstrap` was pointing at `cli.main:app`, which
  returns an int but never raised `SystemExit`, so user errors
  silently exited 0. Now points at `cli.main:main`, which wraps `app`
  in `SystemExit(...)`.
- `pyproject.toml` ruff `per-file-ignores` had no entry for
  `src/bootstrap/api/**`, so legitimate FastAPI `Depends(...)`
  defaults were flagged as B008. Added the ignore.
- `pyproject.toml` `pytest.ini_options` was missing the `asyncio`
  marker registration; `--strict-markers` was rejecting async tests.
- `EvalCase.graders` was unused metadata until now — the
  `OptimizationLoop` now filters graders per case when the field is
  populated, preserving the prior "run everything" behavior when it
  is empty.

### Known gaps (not blocking v0.1.0)

- 9 tests under `tests/sdk/` and `tests/runner/test_otlp_receiver.py`
  require the `telemetry` extra (`uv sync --extra telemetry`) and
  fail without it. They are excluded from the default surface.
- 3 tests under `tests/api/` require the `web` extra
  (`uv sync --extra web`) to install FastAPI.
- Failure modes do not yet survive persistence to SQLite — the
  compare and report tooling already handles their presence gracefully
  for when the schema is extended.
- `CliCommandAdapter` and `HttpEndpointAdapter` are not yet
  auto-wired from YAML; users instantiate them via a Python
  entrypoint. `docs/adapters.md` documents the workaround.

## [0.0.9] - 2026-05-16

### Added — MVP Bloque A reducido: YAML loader + `bootstrap run` end-to-end

Repo loader (`src/bootstrap/repo/`):
- `load_experiment_spec(path)` parses `evals/experiments/<name>.yaml` →
  `(workspace_id, Experiment, [EvalCase], AgentEntrypoint)`. YAML keys
  are 1:1 with the Pydantic field names — no DSL translation; the
  validators do all the shape checking.
- Cases can be inline (`dataset.cases_inline:`) or external JSONL
  (`dataset.cases_path:`). Mutually exclusive; both empty rejected.
- Agent entrypoint declared as `module.path:callable_name`.
  `resolve_agent_callable` defers import until the runner needs it
  (lets `bootstrap inspect` validate a spec without booting user code).
- 14 tests covering inline/external loading, workspace override,
  missing fields, malformed YAML, invalid payloads, entrypoint
  resolution.

CLI `bootstrap run <yaml>`:
- Loads spec → resolves agent callable → wraps as `EmbeddedAdapter`
  (str returns auto-coerced to `AdapterResponse`) → builds the
  proposer per `experiment.proposer.strategy` (grid / random /
  manual) → drives `OptimizationLoop` with `DecisionMatrixEvaluator`
  + `DeterministicGrader` → emits markdown/JSON report.
- Flags: `--workspace`, `--max-iterations`, `--reps`, `--format`,
  `--no-persist`.
- Persists `Experiment` + `IterationRecord` + `DecisionRecord` to
  SQLite when storage is enabled; auto-seeds the workspace row.
- 6 tests covering markdown/JSON output, persistence to SQLite,
  missing-spec error, validation, str→AdapterResponse coercion.

Example experiment:
- `evals/experiments/example_pingpong.yaml` + `evals/datasets/pingpong.jsonl` +
  `bootstrap.examples.pingpong` reference agent. Serves as smoke test
  and onboarding artifact. `uv run bootstrap run evals/experiments/example_pingpong.yaml --no-persist`
  produces a clean report out of the box.

Refactor:
- `DecisionMatrixEvaluator` now inherits from `DecisionEvaluatorProtocol`
  so the type checker recognizes it as a valid argument to
  `OptimizationLoop(decision_evaluator=...)`.

20 new tests (390 total). mypy strict + ruff clean. One new runtime
dep: `pyyaml>=6,<7`.

### Added — Design docs for next implementation surfaces

- `docs/spec/sdk_otlp_design.md`: locked blueprint for the user-side
  SDK façade (`bootstrap.init()`) + embedded OTLP HTTP receiver +
  OpenInference auto-instrumentation. Sections 1-11 cover the
  decisions already made (no re-litigation), package layout, exact
  signatures, span translation table, dependency tree (optional
  extras), test plan, and acceptance criteria. ~1500-2000 LOC budget,
  dedicated session.
- `docs/prompts/web_session_prompt.md`: self-contained prompt for the
  Claude Code session that builds the web UI + SDK + OTLP receiver.
  Includes product vibe (Stripe/Airbnb/ChatGPT/Claude/LangSmith/Mercury),
  page inventory (8 surfaces), design tokens, stack recommendation,
  backend contract, and "done" criteria.

## [0.0.8] - 2026-05-16

### Added — PR 8 + PR 9: Reporter + CLI

Reporter (`bootstrap.reporter`):
- `render_markdown(result)` produces a PR-comment-style summary:
  experiment header (name, goal, state, mode, proposer, iterations
  run, termination reason), target + guardrail spec line, best-
  iteration callout with parameters, per-iteration table
  (`#`, primary, Δ vs running best, decision outcome, rationale —
  with pipe-escaping and 80-char rationale truncation), and a
  top-N failure-modes section drawn from
  `IterationAggregate.failure_mode_counts`.
- `render_json(result)` emits a stable, machine-readable payload
  (`schema_version=1`) keyed on iteration index, with explicit
  best-iteration reference. JSON path is what the CLI's `--format
  json` flag outputs.
- Pure: no I/O, no global state — callers decide where the strings
  end up (stdout, a file, a GitHub PR comment).

CLI (`bootstrap` console script, argparse-only, zero new deps):
- `bootstrap init <slug>` — idempotent workspace seed via
  `seed_workspace`; prints workspace id + member count.
- `bootstrap workspace show <ws_id>` — workspace metadata +
  experiment count.
- `bootstrap experiment list <ws_id>` / `show <ws_id> <exp_id>` —
  inspect experiments in storage with target + iteration progress.
- `bootstrap iteration list <ws_id> <exp_id>` — per-iteration
  primary metric + decision outcome.
- `bootstrap report <ws_id> <exp_id> [--format markdown|json]` —
  reconstructs an OptimizationResult from stored IterationRecords +
  DecisionRecords (lossy on per-case GradeResults, lossless on
  aggregates) and pipes it through the reporter.
- `bootstrap compare <ws_id> <iter_a_id> <iter_b_id>` — side-by-
  side primary metric diff between two iterations of the same
  experiment.
- `bootstrap estimate --cases N --space-size M --reps K
  --cost-per-call X` — dry-run upper-bound on agent calls and
  total USD cost before paying for a run.
- All user-facing errors (missing entity, primary-metric mismatch,
  invalid numeric args) go through `CommandError` → `error: <msg>`
  on stderr → exit code 2. Unexpected exceptions surface as
  tracebacks (bugs, not user errors).

18 new tests (370 total: 9 reporter + 9 CLI). mypy strict + ruff
clean. Zero new runtime deps — argparse + stdlib.

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
