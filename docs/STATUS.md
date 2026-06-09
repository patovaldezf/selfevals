# Status — v0.3.0

This file is the honest snapshot of what selfevals can and cannot do
today. Updated on every release; the CHANGELOG records what _changed_,
this file records what _is_.

## What works end-to-end

- **CLI**: `init`, `workspace`, `experiment`, `iteration`, `report`,
  `run`, `compare`, `estimate`. Every subcommand has a one-line
  description and a copy-paste `Example:` epilog.
- **`selfevals run <spec.yaml>`**: load an experiment spec, resolve
  the agent entrypoint, run cases through an adapter, grade each
  trace, persist iterations to SQLite, render a markdown or JSON
  report.
- **Conversation input**: `EvalCase.input` carries a validated
  multi-turn conversation when it has a `messages` key — roles
  (system/user/assistant/tool), content as a string or a list of
  content blocks, multimodal-aware via the `Modality` enum. Typed
  access through `EvalCase.conversation()`; inputs without a
  `messages` key remain opaque payloads passed to the adapter
  verbatim.
- **Async-first evaluators**: `AgentAdapter.invoke` and
  `Grader.grade` are async. The executor runs repetitions
  concurrently and the optimization loop grades concurrently, both
  bounded by configurable semaphores (`concurrency` /
  `grade_concurrency`, default 8). `asyncio.run` lives only at the
  CLI edge.
- **Adapters**: `EmbeddedAdapter` (sync or async Python callable),
  `CliCommandAdapter` (async subprocess JSON), `HttpEndpointAdapter`
  (native async on httpx). All three are auto-wired from YAML via the
  `agent:` block — `agent: {type: embedded|cli|http, ...}` (the bare
  `entrypoint:` form stays as the embedded shorthand).
- **Graders**: `DeterministicGrader` (`must_include`,
  `must_not_include`, `required_tools`, `forbidden_tools`,
  `regex_match`, `structured_output` equality, `output_schema` JSON
  Schema), `SetMatchGrader` (many-to-many set scoring —
  completeness/precision/recall/F1 over `structured_output["detected"]`
  vs `Expected.must_include`, with per-element funnel and case-level
  `aliases`; declarable as `type: set_match`), `LLMJudgeGrader` (single
  judge, rubric template, optional `GraderCard`-driven calibration with
  auto-degrade to advisory when thresholds breach), `JudgePanelGrader`
  (N judges + consensus, declarable as `type: judge_panel`; default 3 /
  majority). Calibration utilities compute precision/recall/F1/macro-F1
  and high-risk false negatives.
- **OptimizationLoop**: `ManualProposer`, `GridProposer`,
  `RandomProposer`, `LLMProposer` (offline deterministic hypothesis mode
  by default; LLM mode via an injected `AgentAdapter`). Convergence
  detection. Aggregation across reps. Best-iteration selection.
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

- `GradeResult` is flat: `label + score + reason + failure_modes`.
  No `breakdown` for funnel-style multi-level scoring yet (see the
  roadmap section below).
- Failure-mode counts now persist on
  `IterationMetrics.failure_mode_counts` (keyed by stable mode
  identity), so the compare/report tooling shows real data and
  per-mode trends are queryable across iterations.
- `Annotation` exists as a schema but there is no UI workflow for
  collecting human labels. The CLI ingests them via direct API.

### Adapters

- All three adapters auto-wire from YAML via `agent: {type: ...}`.
  Custom (user-subclassed) adapters still wire via a Python
  `entrypoint`; `docs/adapters.md` documents the pattern.
- The `llm_judge` `judge_entrypoint` fallback only applies when the
  agent is `embedded`; cli/http agents must name a `judge_entrypoint`
  explicitly.
- `HttpEndpointAdapter` has no retries and no streaming (it is native
  async on httpx; `headers` and `timeout_seconds` are configurable from
  YAML, but retry/backoff is not exposed yet).

### Optimization

- Only `manual`, `grid`, `random`, and `llm_proposer` proposers. No
  Bayesian, no bandit, no evolutionary, no streaming proposers.
- No pairwise comparison or multi-judge consensus (single judge
  only — the `GraderCard` schema is panel-ready).
- Convergence detection is delta-based; there is no learned
  stopping criterion.

### API and web UI

- `src/selfevals/api/` ships endpoints under FastAPI but the write
  side is minimal (workspace creation is the only POST). All
  experiment lifecycle goes through the CLI.
- `web/` (SvelteKit) is scaffolded but not feature-complete.
  `selfevals serve` is referenced in some prompts but no
  `cmd_serve` exists in `commands.py` today.

### Telemetry and OTel

- SDK auto-instrumentation works for known providers (Anthropic,
  OpenAI, Bedrock) when their OTel adapters are installed via the
  `telemetry` extra. Without the extra, the SDK initializes as a
  no-op.
- 24 tests under `tests/sdk/` (18) and
  `tests/runner/test_otlp_receiver.py` (6) require
  `uv sync --extra telemetry` to pass.

## How to run the test suite cleanly

```bash
# Default surface — 559 passed.
uv sync && uv run pytest --ignore=tests/api --ignore=tests/sdk \
  --ignore=tests/runner/test_otlp_receiver.py

# Full surface — install optional extras first. 597 passed.
uv sync --extra telemetry --extra web && uv run pytest
```

## Roadmap

The roadmap is driven by evaluating real conversational agents. Each
release closes the gaps that block the next scenario; the rest stays
on the backlog until it earns its place.

### Shipped in 0.3.0

- **Validated multi-turn conversation input** — `EvalCase.input` now
  enforces a typed conversation shape when it carries `messages`,
  with multimodal-aware content blocks and typed access via
  `EvalCase.conversation()`.
- **Async-first evaluators** — one async contract for adapters and
  graders, concurrent repetitions and grading bounded by
  configurable semaphores, native-async HTTP adapter on httpx.

### Shipped in 0.2.x

- **Provider extras bundle the provider SDK** (0.2.1) — a single
  `pip install selfevals[anthropic]` (or `[openai]`, etc.) pulls the
  SDK _and_ the tracing integration.
- **OpenAI example** (`examples/hello_openai/`, 0.2.1) — an OpenAI
  twin of the Anthropic example, with a deterministic fake fallback.
- **Onboarding docs** (0.2.2) — rewritten README, `examples/README.md`
  walk-through, expanded `CONTRIBUTING.md`, and the `bootstrap` ->
  `selfevals` rename swept through the CLI, CI, and bundled skill.

### Still on the backlog

- `breakdown: dict[str, Any]` on `GradeResult` for funnel-style scores.
- A `selfevals dataset import` CLI command that pulls EvalCases from
  a SQL source.
- Retries and timeout configuration on `HttpEndpointAdapter`.
- A `serve` CLI that mounts the FastAPI app and the optimization
  loop concurrently.
