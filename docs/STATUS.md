# Status — v0.10.0

This file is the honest snapshot of what selfevals can and cannot do
today. Updated on every release; the CHANGELOG records what _changed_,
this file records what _is_.

## What works end-to-end

- **CLI**: `init`, `workspace`, `experiment`, `iteration`, `report`,
  `run`, `compare`, `estimate`, `baseline`, `regression`. Every
  subcommand has a one-line description and a copy-paste `Example:`
  epilog.
- **Regression gate + dataset baseline**: the first run that completes
  on a dataset auto-registers its best iteration as that dataset's
  baseline (`DatasetBaseline`, idempotent — a later better run does not
  move it). `selfevals regression check <ws> --dataset <ds>
--iteration <itr>` compares the current iteration against the dataset
  baseline and exits `0` (ok) / `1` (regression) / `2` (usage error),
  so CI can gate on it. It flags drops in primary/pass@1, per-class F1
  of the confusion matrix, and (optionally) error-rate rises.
  `selfevals baseline show|set` inspects or re-baselines explicitly.
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
  `grade_concurrency`). As of SF-3 these are wired from
  `run.parallelism` (YAML, default 8, `ge=1 le=64`) — previously dead
  code, with the semaphores hardcoded to 8 (the default stays 8 so
  specs that don't declare it keep the legacy concurrency).
  `asyncio.run` lives only at the CLI edge.
- **Adapters**: `EmbeddedAdapter` (sync or async Python callable),
  `CliCommandAdapter` (async subprocess JSON), `HttpEndpointAdapter`
  (native async on httpx). All three are auto-wired from YAML via the
  `agent:` block — `agent: {type: embedded|cli|http, ...}` (the bare
  `entrypoint:` form stays as the embedded shorthand).
- **Graders**: `DeterministicGrader` (`must_include`,
  `must_not_include`, `required_tools`, `forbidden_tools`,
  `regex_match`, `structured_output` equality, `output_schema` JSON
  Schema), `SetMatchGrader` (many-to-many set scoring —
  completeness/precision/recall/F1 of the detected set vs
  `Expected.must_include`, with per-element funnel and case-level
  `aliases`; declarable as `type: set_match`. The detected set is read
  via the path selector through `extract` — default `"detected"`, but
  any slice works, e.g. `"candidates[].id"`), `LLMJudgeGrader` (single
  judge, rubric template, optional `GraderCard`-driven calibration with
  auto-degrade to advisory when thresholds breach), `JudgePanelGrader`
  (N judges + consensus, declarable as `type: judge_panel`; default 3 /
  majority), `FunnelGrader` (declarable as `type: funnel`: N sequential
  levels, each extracting a `structured_output`/trace slice via a path
  selector and scoring it with a builtin match or any nested grader, with
  gate short-circuit and per-level failure modes), `ClassificationGrader`
  (single-label N-class scoring, declarable as `type: confusion`: extracts
  the predicted class via the path selector and compares it to the case's
  expected class, emitting the `(expected, predicted)` pair so the
  aggregator rolls up an NxN confusion matrix + per-class P/R/F1 + macro-F1,
  rendered in the markdown report). Calibration utilities compute
  precision/recall/F1/macro-F1 and high-risk false negatives — the confusion
  math is shared via `graders/_confusion.py` so F1 is defined once.
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
- **Datasets** (v0.9.0): first-class, persisted, reusable across
  experiments. `repo/datasets.py::persist_dataset` is the canonical
  create path (manifest hash + statistics) shared by the CLI
  (`selfevals dataset create/import/list/show/freeze`), the API
  (`/workspaces/{ws}/datasets` — list/detail/create/upload/freeze,
  three upload modes), and inline materialization at launch. An
  experiment consumes a dataset inline (`dataset.cases_*`, materialized
  to a real Dataset on run) or by reference (`dataset: {ref: ds_xxx}`,
  or `--dataset`/`dataset_id` at launch). The dataset's
  `split_allocation` now reaches the loop, so `sample_strategy`
  (`random_subset`/`stratified`) and holdout actually run.
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

- `GradeResult` carries an optional `breakdown: BreakdownNode | None`
  (a weighted recursive tree) on top of the flat
  `label + score + reason + failure_modes`. `FunnelGrader` populates
  it today; the top-level `label`/`score` stay authoritative. Most
  other graders still return a flat result (no breakdown), so the
  drill-down is only as deep as the graders that opt in.
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

- `src/selfevals/api/` ships read endpoints plus a growing write side:
  create workspace, launch an experiment run (`POST
.../experiments/run`, background + 202), and full dataset CRUD
  (create/upload/freeze). Experiment authoring/editing beyond launch
  still goes through the CLI.
- `web/` (SvelteKit) is scaffolded but not feature-complete.
  `selfevals serve` exists (`cmd_serve` mounts the FastAPI app) but the
  web UI it serves is still partial.

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

### Shipped in 0.10.0

- **`funnel` grader** — declarative N-level scoring (`type: funnel`):
  each level extracts a `structured_output`/trace slice via the path
  selector and scores it with a builtin match or any nested grader,
  with `gate` short-circuit and a recursive `breakdown` tree that the
  aggregator and reporter drill into.
- **`SetMatchGrader.extract`** — the detected set is read through the
  path selector (default `"detected"`), so a `set_match` can target any
  slice of the contract without knowing its shape. Backwards-compatible.
- **`showcase` example** — a second copyable example
  (`selfevals examples copy showcase`) that exercises every grader type
  and funnel match kind, fully offline and deterministic.

### Shipped in 0.9.0

- **Datasets as a first-class entity** — `Dataset` is persisted and
  reused across experiments; one canonical `persist_dataset` path shared
  by CLI, API, and inline materialization at launch. The dataset's
  `split_allocation` now reaches the loop.
- **`set_match` and `judge_panel` graders** — many-to-many set scoring
  and an N-judge consensus panel, both declarable in YAML.

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

- Importing a dataset from a SQL/production source. `selfevals dataset
import` exists for JSONL files (v0.9.0); pulling EvalCases straight
  from a SQL query is still backlog.
- Retries and timeout configuration on `HttpEndpointAdapter`.
- A feature-complete web UI behind `selfevals serve` (the CLI and the
  FastAPI mount exist; the SvelteKit app is still partial).
