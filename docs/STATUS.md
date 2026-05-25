# Status — v0.1.0

This file is the honest snapshot of what bootstrap can and cannot do
today. Updated on every release; the CHANGELOG records what *changed*,
this file records what *is*.

## What works end-to-end

- **CLI**: `init`, `workspace`, `experiment`, `iteration`, `report`,
  `run`, `compare`, `estimate`. Every subcommand has a one-line
  description and a copy-paste `Example:` epilog.
- **`bootstrap run <spec.yaml>`**: load an experiment spec, resolve
  the agent entrypoint, run cases through an adapter, grade each
  trace, persist iterations to SQLite, render a markdown or JSON
  report.
- **Adapters**: `EmbeddedAdapter` (Python callable), `CliCommandAdapter`
  (subprocess JSON), `HttpEndpointAdapter` (POST JSON). All three
  callable from Python; only `EmbeddedAdapter` is auto-wired from
  YAML today.
- **Graders**: `DeterministicGrader` (`must_include`,
  `must_not_include`, `required_tools`, `forbidden_tools`,
  `regex_match`, `structured_output` equality, `output_schema` JSON
  Schema), `LLMJudgeGrader` (single judge, rubric template, optional
  `GraderCard`-driven calibration with auto-degrade to advisory when
  thresholds breach). Calibration utilities compute
  precision/recall/F1/macro-F1 and high-risk false negatives.
- **OptimizationLoop**: `ManualProposer`, `GridProposer`,
  `RandomProposer`. Convergence detection. Aggregation across reps.
  Best-iteration selection.
- **DecisionMatrix**: guardrail check → first-iteration absolute
  target → improvement-vs-baseline. Emits `KEEP_CANDIDATE`, `REJECT`,
  `INVESTIGATE`, `SPAWN_SUBEXPERIMENT`, `REQUIRE_TRADEOFF_REVIEW`
  with rationale.
- **Storage**: SQLite-backed with optimistic concurrency, workspace
  isolation, migrations. Filesystem object store for blobs.
- **Reporter**: markdown (PR-friendly) and JSON. Cost & time
  summaries appear when data is present, omitted gracefully when
  not. Compare two iterations with proposal diff, metrics diff,
  failure-mode diff, and a winner recommendation.
- **Friendly errors**: YAML parse errors with hints, missing
  datasets with fuzzy-match suggestions, unknown graders listing
  what is available, HTTP adapter transport errors with the URL,
  SQLite locked / corrupted cases. Exit code 2 for user errors.

## What does not work yet

### Schema and runtime

- `EvalCase.input` is `dict[str, Any]` — opaque. Conversation
  multi-turn evals will fit but the schema does not enforce shape.
- `GradeResult` is flat: `label + score + reason + failure_modes`.
  No `breakdown` for funnel-style multi-level scoring (planned for
  v0.2 once seals dogfooding identifies the exact shape).
- Failure-mode counts do not survive persistence — they exist on
  fresh `OptimizationResult` objects but `IterationMetrics` does
  not carry them. The compare and report tooling already handles
  their presence gracefully for when this is fixed.
- `Annotation` exists as a schema but there is no UI workflow for
  collecting human labels. The CLI ingests them via direct API.

### Adapters

- `CliCommandAdapter` and `HttpEndpointAdapter` are not yet
  auto-wired from YAML. Users instantiate them via a Python
  entrypoint; `docs/adapters.md` documents the pattern.
- `HttpEndpointAdapter` has no retries, no streaming, no per-request
  headers.

### Optimization

- Only `manual`, `grid`, and `random` proposers. No Bayesian, no
  evolutionary, no streaming proposers.
- No pairwise comparison or multi-judge consensus (single judge
  only — the `GraderCard` schema is panel-ready).
- Convergence detection is delta-based; there is no learned
  stopping criterion.

### API and web UI

- `src/bootstrap/api/` ships endpoints under FastAPI but the write
  side is minimal (workspace creation is the only POST). All
  experiment lifecycle goes through the CLI.
- `web/` (SvelteKit) is scaffolded but not feature-complete.
  `bootstrap serve` is referenced in some prompts but no
  `cmd_serve` exists in `commands.py` today.

### Telemetry and OTel

- SDK auto-instrumentation works for known providers (Anthropic,
  OpenAI, Bedrock) when their OTel adapters are installed via the
  `telemetry` extra. Without the extra, the SDK initializes as a
  no-op.
- 9 tests under `tests/sdk/` and `tests/runner/test_otlp_receiver.py`
  require `uv sync --extra telemetry` to pass.

## How to run the test suite cleanly

```bash
# Default surface — 481 passed.
uv sync && uv run pytest --ignore=tests/api --ignore=tests/sdk \
  --ignore=tests/runner/test_otlp_receiver.py

# Full surface — install optional extras first.
uv sync --extra telemetry --extra web && uv run pytest
```

## What v0.2 will probably contain

The roadmap for v0.2 is driven by dogfooding against
[seals](../../../seals%20ideas/chat-repo/seals) — concretely,
optimizing the system prompt of Valentina (the sales agent) against
real scenarios from Supabase. The following items are predicted gaps
that the dogfooding will confirm or refute:

- Conversation multi-turn shape on `EvalCase.input`.
- `breakdown: dict[str, Any]` on `GradeResult` for funnel-style scores.
- YAML wiring for `HttpEndpointAdapter` (no Python entrypoint
  required).
- A `bootstrap dataset import` CLI command that pulls EvalCases from
  Supabase or any SQL source.
- Retries and timeout configuration on `HttpEndpointAdapter`.
- A `serve` CLI that mounts the FastAPI app and the optimization
  loop concurrently.

When dogfooding reveals which of these actually hurt, they become
v0.2 features. The rest stays on the backlog.
