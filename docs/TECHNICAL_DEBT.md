# selfevals technical debt audit

Date: 2026-06-23

> ## Resolved by the Postgres-only migration (branch `feat/postgres-only`)
>
> The storage/persistence debt below was the trigger for migrating selfevals to a
> **Postgres-only, relational-canonical** backend. These items are now resolved:
>
> - **#1 (SQLite not relational)** — N/A. SQLite is removed; there is no generic
>   `entities` table. Every entity has its own typed table(s).
> - **#2 (Postgres looks relational but doesn't enforce)** — resolved. A
>   forward-only migration runner (`storage/postgres/migrations/`, table
>   `_pg_migrations`) owns all DDL; the schema is split into versioned migrations
>   (m0001–m0006); FK / CHECK / UNIQUE constraints are enforced; CI runs a
>   Postgres service.
> - **#3 (dual source of truth: JSON + projections)** — resolved. The relational
>   rows are the single source of truth (per-field columns + child tables; JSONB
>   only for genuinely schemaless fields). No JSON-payload shadow copy.
> - **#5 (optimistic concurrency not atomic)** — resolved. `put_entity` uses an
>   atomic compare-and-swap on `version`; `StorageInterface.transaction()` gives
>   all-or-nothing multi-entity writes.
> - **Dataset API write paths were SQLite-only** — resolved. `api/dataset_writer.py`
>   uses the injected storage (`open_storage`), no hardcoded `SQLiteStorage`.
> - **Multi-entity writes not atomic** — resolved via `transaction()` (used by
>   dataset persist and the migrate path).
> - **Hot-method fallback pattern / metrics duplicated in scans+SQL** — resolved.
>   The 14 query/metric methods are on `StorageInterface`; `api/queries.py` and
>   `api/metrics.py` call them directly (no `getattr` discovery, no in-memory scan).
> - **Postgres not exercised by CI** — resolved. `ci.yml` runs `postgres:16` in the
>   python and e2e jobs; the suite runs against real Postgres (pytest-postgresql).
> - **Repeated storage-close pattern** — resolved for the API via a `yield`
>   dependency that closes the connection after each response.
> - **Missing `.env.example`** — added.
>
> Frontend, large-file, and remaining modularity items below are out of scope for
> this migration and still stand.

This document records the technical debt found during a static repo audit plus
local verification runs. It focuses on persistence, modularity, DRY violations,
large files, frontend maintainability, CI/tooling, and documentation drift.

The repo is in a strong baseline state for Python correctness: `ruff`,
`mypy --strict`, and the full Python test suite pass. The main risk is not
basic type safety. The risk is that several central files have grown into
coordination hubs, and the storage model still carries MVP tradeoffs that make
scale, integrity, and production operations harder.

## Verification Snapshot

Commands run locally:

- `uv run ruff check .`: passed.
- `uv run mypy src/selfevals`: passed.
- `uv run pytest`: `1229 passed, 1 skipped`.
- `web`: `npm run check`: passed.
- `web`: `npm run build`: passed.
- `web`: `npm run lint`: failed due to Prettier formatting in:
  - `web/src/lib/api/client.ts`
  - `web/src/routes/[workspace]/clusters/+page.svelte`
- `landing`: `npm run build`: passed outside the sandbox.
- `landing`: `npm run lint`: failed because `next lint` is no longer a valid
  working lint command for this Next 16 setup.

The skipped Python test is `tests/storage/test_factory.py::test_postgres_storage_contract_smoke`,
which requires `SELFEVALS_TEST_POSTGRES_URL`.

## Highest Priority Debt

### 1. SQLite storage is not relational

Location: `src/selfevals/storage/migrations/m0001_initial.py`

The main SQLite schema stores every Pydantic entity in a single generic table:

- `entities(entity_type, id, workspace_id, version, created_at, updated_at, deleted_at, payload)`
- `objects(pointer, workspace_id, key, content_hash, byte_size, created_at)`

Debt:

- No domain tables for `workspaces`, `experiments`, `datasets`, `eval_cases`,
  `iterations`, `traces`, `grader_results`, `run_jobs`, etc.
- No `FOREIGN KEY` constraints.
- No `UNIQUE` constraints for logical uniqueness such as workspace slug,
  experiment iteration number, dataset baseline, run id, or member assignment.
- No `CHECK` constraints for enum-like fields.
- No DB-level prevention of orphaned cases, traces, iterations, decisions,
  baselines, jobs, or members.
- Filtering hot fields depends on JSON extraction.

Impact:

- Data integrity depends almost entirely on Pydantic and application code.
- The DB can accept impossible states if data is inserted by a bug, old code,
  script, or migration.
- Query performance degrades as persisted traces and cases grow.
- Auditing data quality requires loading JSON, not inspecting relational state.

Recommended direction:

- Keep SQLite simple for local use if desired, but introduce at least relational
  tables for hot production entities.
- Add domain migrations for `workspaces`, `experiments`, `datasets`,
  `eval_cases`, `iterations`, `traces`, `trace_grader_results`, `run_jobs`,
  and `members`.
- Add constraints for logical invariants:
  - `UNIQUE(owner_id, slug)` or `UNIQUE(slug)` depending on tenant semantics.
  - `UNIQUE(workspace_id, experiment_id, iteration)`.
  - `UNIQUE(workspace_id, dataset_id)` for baselines if one baseline per dataset
    remains the contract.
  - `CHECK(status IN (...))` for lifecycle/status columns.
  - `FOREIGN KEY` relationships once entity IDs live in relational tables.

### 2. Postgres looks relational but does not enforce relationships

Location: `src/selfevals/storage/postgres.py`

Postgres projects hot entities into tables:

- `workspaces`
- `experiments`
- `iterations`
- `eval_cases`
- `traces`
- `trace_spans`
- `llm_calls`
- `tool_calls`
- `trace_grader_results`
- `decisions`
- `run_jobs`

Debt:

- Tables have primary keys and indexes, but no `FOREIGN KEY` constraints.
- No `ON DELETE` behavior.
- No `CHECK` constraints for states/statuses.
- No logical uniqueness constraints for common invariants.
- The schema is a giant `_SCHEMA_SQL` string executed with `CREATE TABLE IF NOT EXISTS`.
- There is no Postgres migration runner.

Impact:

- Postgres is used as a query accelerator more than a source of relational truth.
- A trace can reference a missing experiment/case.
- A decision can reference a missing iteration.
- A job can reference a missing experiment.
- Data can be deleted from projections without cascading to child facts.
- Production schema evolution has no versioned path.

Recommended direction:

- Split Postgres schema into versioned migrations.
- Add a Postgres migration table and forward-only runner.
- Add FKs and constraints after cleaning existing data.
- Move DDL out of `postgres.py`.
- Add CI coverage using a Postgres service container.

### 3. Dual source of truth: JSON entities plus projected tables

Locations:

- `src/selfevals/storage/postgres.py::_upsert_hot_projection`
- `src/selfevals/storage/postgres.py::_replace_trace_facts`

The canonical entity is still stored in `entities.payload`, while selected
fields are projected into hot relational tables.

Debt:

- Projection logic is manually maintained.
- Every new hot field requires editing `postgres.py`.
- If projection logic fails, `entities` can contain data that query helpers do
  not see.
- The code has no generalized projection registry or contract tests comparing
  generic and hot paths.

Impact:

- Query behavior can differ between SQLite and Postgres.
- Adding a new entity type or field is easy to forget in Postgres projections.
- Schema drift can become silent.

Recommended direction:

- Introduce projection modules per entity, for example:
  - `storage/postgres/projections/workspace.py`
  - `storage/postgres/projections/experiment.py`
  - `storage/postgres/projections/trace.py`
- Add backend equivalence tests for query methods.
- Decide which source is canonical per entity. If Postgres is production
  storage, make relational rows canonical for hot entities and keep JSON as
  audit payload, not the only truth.

### 4. Auth and authorization are stubs

Locations:

- `src/selfevals/api/app.py`
- `web/src/lib/api/client.ts`
- `docs/FRONTEND.md`

The API accepts `X-SelfEvals-User`; the frontend hardcodes `local`.

Debt:

- No real authentication.
- No workspace membership enforcement in API handlers.
- Role entities exist, but they are not enforced as permissions.
- Mutating endpoints are callable by anyone who can reach the service.

Impact:

- Safe for local-only MVP, unsafe for shared deployments.
- Workspace isolation is caller-controlled through URL params.
- Role model can give false confidence because it is present but not enforced.

Recommended direction:

- Add an auth dependency that resolves a real user principal.
- Add an authorization dependency for workspace-level access.
- Enforce roles for mutations:
  - viewer: read-only.
  - editor: run experiments, edit datasets/cases/failure modes.
  - owner: membership and destructive lifecycle operations.
- Add tests for cross-workspace access denial.

### 5. Optimistic concurrency is not atomic

Locations:

- `src/selfevals/storage/sqlite.py`
- `src/selfevals/storage/postgres.py`

Both storage backends read the current version and then update in a separate
statement. Same-version writes are also allowed.

Debt:

- Race windows exist between version read and write.
- Same-version writes can mask accidental stale writes.
- Updates do not use `WHERE version = expected_version` with row count checks.

Impact:

- Concurrent background jobs or API mutations can overwrite each other.
- The concurrency contract is weaker than its name suggests.

Recommended direction:

- Use compare-and-swap style updates:
  - `UPDATE ... WHERE entity_type = ? AND id = ? AND version = ?`
  - verify exactly one row changed.
- Define whether same-version idempotent writes are truly allowed.
- If allowed, restrict same-version writes to byte-identical payloads.

## Large Files And Modularity Debt

The repo has several production files that are too large for comfortable
maintenance. Size alone is not the problem; the issue is mixed responsibilities.

### Largest production files

- `src/selfevals/api/app.py`: 1419 lines.
- `src/selfevals/storage/postgres.py`: 1373 lines.
- `src/selfevals/repo/loader.py`: 1120 lines.
- `web/src/routes/[workspace]/experiments/[experiment]/+page.svelte`: 1080 lines.
- `web/src/lib/api/client.ts`: 1073 lines.
- `src/selfevals/api/queries.py`: 1060 lines.
- `src/selfevals/runner/launch.py`: 920 lines.
- `src/selfevals/api/schemas.py`: 863 lines.
- `src/selfevals/optimization/loop.py`: 764 lines.
- `src/selfevals/cli/commands.py`: 678 lines.
- `src/selfevals/cli/main.py`: 677 lines.
- `src/selfevals/optimization/aggregator.py`: 652 lines.
- `src/selfevals/trace/recorder.py`: 625 lines.
- `src/selfevals/graders/judge_panel.py`: 572 lines.
- `src/selfevals/api/metrics.py`: 530 lines.
- `web/src/routes/[workspace]/traces/[trace]/+page.svelte`: 522 lines.

### Largest functions/classes

- `src/selfevals/api/app.py::build_app`: 1241 lines.
- `src/selfevals/cli/main.py::_build_parser`: 635 lines.
- `src/selfevals/storage/postgres.py::PostgresStorage`: 493 lines.
- `src/selfevals/optimization/loop.py::OptimizationLoop`: 476 lines.
- `src/selfevals/trace/recorder.py::TraceRecorder`: 431 lines.
- `src/selfevals/graders/judge_panel.py::JudgePanelGrader`: 405 lines.
- `src/selfevals/runner/executor.py::Executor`: 343 lines.
- `src/selfevals/graders/trajectory.py::TrajectoryGrader`: 249 lines.
- `src/selfevals/storage/postgres.py::_upsert_hot_projection`: 229 lines.

### API app is a router monolith

Location: `src/selfevals/api/app.py`

Debt:

- `build_app()` defines CORS, lifespan, storage factories, object store setup,
  and all route handlers.
- Around 50 handlers are nested inside one function.
- Most handlers repeat the same `try/finally storage.close()` pattern.
- Domain concerns are mixed in one file:
  - workspaces
  - datasets
  - failure modes
  - baselines
  - analysis
  - metrics
  - experiments
  - iterations
  - traces
  - streams
  - payload resolution

Impact:

- Any API change requires opening a giant file.
- Merge conflicts become likely.
- Reuse of dependencies and error mapping is harder.
- Route-level ownership is unclear.

Recommended direction:

- Split into routers:
  - `api/routes/workspaces.py`
  - `api/routes/datasets.py`
  - `api/routes/failure_modes.py`
  - `api/routes/baselines.py`
  - `api/routes/analysis.py`
  - `api/routes/metrics.py`
  - `api/routes/experiments.py`
  - `api/routes/iterations.py`
  - `api/routes/traces.py`
  - `api/routes/runs.py`
- Add `api/dependencies.py` with storage/object-store/user/workspace auth.
- Use a dependency with `yield` to close storage automatically.

### Postgres backend is too broad

Location: `src/selfevals/storage/postgres.py`

Debt:

- One file contains:
  - connection lifecycle
  - DDL schema
  - workspace helpers
  - experiment query helpers
  - metrics SQL
  - generic entity storage
  - projection writes
  - trace fact extraction
  - deletion cleanup
  - utility row fetchers

Impact:

- Storage changes and query changes are coupled.
- Projection logic is hard to test in isolation.
- The file is large enough that adding one entity encourages more in-place
  growth.

Recommended direction:

- Split into:
  - `storage/postgres/storage.py`
  - `storage/postgres/schema.py`
  - `storage/postgres/migrations/`
  - `storage/postgres/projections.py`
  - `storage/postgres/trace_facts.py`
  - `storage/postgres/metrics.py`
  - `storage/postgres/queries.py`

### Loader has too much manual parsing

Location: `src/selfevals/repo/loader.py`

Debt:

- YAML parsing, validation, model construction, serialization, agent parsing,
  dataset parsing, and grader-specific parsing live together.
- There are many repeated `isinstance` checks and hand-written error messages.
- Grader config parsing is spread across `_build_grader_specs` and multiple
  `_parse_*_params` helpers.

Impact:

- Adding a new grader adds more branches to the same file.
- Validation logic is harder to reuse from API or tests.
- The authoring config schema is implicit rather than a typed object model.

Recommended direction:

- Define Pydantic authoring models for YAML config.
- Keep a mapper from authoring models to canonical runtime entities.
- Split grader config parsing by grader type.
- Keep filesystem/path resolution separate from validation.

### Runner launch is a registry and wiring hub

Location: `src/selfevals/runner/launch.py`

Debt:

- Adapter construction, proposer construction, grader factory registration,
  dataset materialization, workspace checks, and loop construction live in one
  file.
- Grader-specific factories accumulate in this module.

Impact:

- Every new grader/adaptor/dataset behavior grows the same file.
- The launch path owns too many extension points.

Recommended direction:

- Move grader factories to `runner/grader_factories/`.
- Move dataset materialization to `runner/datasets.py`.
- Move adapter/proposer builders to dedicated modules.
- Keep `build_loop()` as orchestration only.

### Optimization loop is too central

Location: `src/selfevals/optimization/loop.py`

Debt:

- The loop manages iteration lifecycle, case execution, aggregation, decisions,
  trace persistence, multi-turn collapse, error analysis hooks, and record
  construction.

Impact:

- Behavior changes can regress unrelated areas.
- It is harder to test run lifecycle pieces in isolation.

Recommended direction:

- Extract:
  - iteration runner
  - persistence writer
  - decision evaluator
  - trace persistence policy
  - error-analysis hook
  - convergence policy

## DRY Debt

### Repeated storage close pattern

Locations:

- `src/selfevals/api/app.py`
- `src/selfevals/cli/commands.py`
- `src/selfevals/cli/dataset_commands.py`
- `src/selfevals/cli/baseline_commands.py`
- `src/selfevals/cli/analyze_commands.py`

Pattern:

```python
storage = ...
try:
    ...
finally:
    storage.close()
```

Recommended direction:

- FastAPI: dependency with `yield`.
- CLI: shared context helper or `with closing(open_storage(...))`.

### Hot-method fallback pattern

Locations:

- `src/selfevals/api/queries.py`
- `src/selfevals/api/metrics.py`
- `src/selfevals/storage/postgres.py`

Pattern:

- API checks `getattr(storage, "some_hot_method", None)`.
- If present, Postgres runs optimized SQL.
- Otherwise SQLite path scans generic JSON entities.

Debt:

- Backend capability discovery is ad hoc.
- Query logic is split across API and storage.
- It is not obvious which queries are guaranteed by the storage interface.

Recommended direction:

- Introduce explicit query service interfaces:
  - `WorkspaceQueries`
  - `ExperimentQueries`
  - `TraceQueries`
  - `MetricsQueries`
  - `DatasetQueries`
- Provide SQLite and Postgres implementations.
- Add contract tests that compare outputs for seeded fixtures.

### Manual TypeScript API types duplicate Pydantic schemas

Location: `web/src/lib/api/client.ts`

The file explicitly says the frontend types mirror `selfevals.api.schemas`
manually.

Debt:

- Types can drift silently from FastAPI/Pydantic responses.
- All endpoints and all DTO types live in one large file.
- Adding an API field requires touching both Python and TypeScript manually.

Recommended direction:

- Generate TypeScript types from `/api/openapi.json` using `openapi-typescript`
  or an equivalent tool.
- Split API modules:
  - `lib/api/request.ts`
  - `lib/api/types.ts`
  - `lib/api/workspaces.ts`
  - `lib/api/experiments.ts`
  - `lib/api/datasets.ts`
  - `lib/api/metrics.ts`
  - `lib/api/traces.ts`

### Metrics logic duplicated in Python scans and SQL

Locations:

- `src/selfevals/api/metrics.py`
- `src/selfevals/storage/postgres.py`

Debt:

- Pass rate, failure mode, tool, cost, token, and latency metrics exist as
  in-memory trace scans and as Postgres SQL.
- Ordering, null handling, totals, and filters must stay equivalent by hand.

Recommended direction:

- Add fixture-level equivalence tests for SQLite fallback and Postgres hot path.
- Isolate common response construction from row production.
- Keep backend-specific code focused on producing normalized metric rows.

## Frontend Modularity Debt

### Experiment detail page is a multi-feature component

Location: `web/src/routes/[workspace]/experiments/[experiment]/+page.svelte`

The component handles:

- header and breadcrumbs
- experiment state display
- run cancel flow
- polling
- live run lookup
- SSE live span pulse
- iterations table
- results tab
- compare tab
- funnel tab
- decisions tab
- iteration drawer
- formatting helpers

Debt:

- 1080 lines in one Svelte file.
- 332-line `<script>` block.
- Several independent async/lazy loading concerns share local state.
- Token guards for compare/funnel/results are repeated.

Recommended split:

- `ExperimentHeader.svelte`
- `ExperimentTabs.svelte`
- `IterationsTab.svelte`
- `ResultsTab.svelte`
- `CompareTab.svelte`
- `FunnelTab.svelte`
- `DecisionsTab.svelte`
- `IterationDrawer.svelte`
- `useExperimentPolling.ts`
- `useLiveRunPulse.ts`
- `useLazyRequest.ts`

### Trace page mixes inspection and dataset promotion

Location: `web/src/routes/[workspace]/traces/[trace]/+page.svelte`

The component handles:

- trace metadata
- span tree
- selected span details
- pointer-field discovery
- pointer rendering
- SSE trace updates
- promote trace to regression case
- dataset creation/append flow

Debt:

- Trace inspection and dataset authoring are separate workflows in one page.
- Promotion modal state increases complexity of the trace viewer.

Recommended split:

- `TraceHeader.svelte`
- `TraceSpanPanel.svelte`
- `SpanDetailPanel.svelte`
- `PointerPayloads.svelte`
- `PromoteTraceModal.svelte`
- `useTraceStream.ts`

### Frontend client is a manual SDK

Location: `web/src/lib/api/client.ts`

Debt:

- 1073 lines.
- API types, request wrapper, error wrapper, query-string helper, and all
  endpoint methods live together.
- Hardcoded `X-SelfEvals-User: local`.

Recommended direction:

- Generate types.
- Split API methods by resource.
- Centralize auth header injection in one request layer.

## Test Modularity Debt

The Python suite is broad and passes, but several test files are large enough to
slow future changes.

Largest test files:

- `tests/repo/test_loader.py`: 838 lines.
- `tests/runner/test_launch_wiring.py`: 835 lines.
- `tests/optimization/test_loop.py`: 791 lines.
- `tests/optimization/test_aggregator.py`: 729 lines.
- `tests/runner/test_executor.py`: 573 lines.
- `tests/reporter/test_markdown.py`: 512 lines.
- `tests/graders/test_judge_panel.py`: 500 lines.

Debt:

- Large files mix multiple behavioral domains.
- Fixture builders are often local to files.
- Adding new behavior increases already-large files.

Recommended direction:

- Split by behavior, not by implementation module where files exceed ~500 lines.
- Extract shared builders to `tests/builders/` or domain-specific fixture modules.
- Keep integration tests broad, but move edge-case matrices into focused files.

## Documentation Drift

Locations:

- `docs/FRONTEND.md`
- `docs/FE_FASE_A_PENDIENTES.md`
- `docs/ROADMAP.md`
- `README.md`

Observed drift:

- `docs/FRONTEND.md` says `/[workspace]/clusters` and `/[workspace]/datasets`
  are stubs, but the code now implements those pages.
- `docs/FE_FASE_A_PENDIENTES.md` still marks A8 as in progress.
- `README.md` says current version `0.9.0`, while `pyproject.toml` declares
  `0.12.0`.
- Some roadmap entries describe capabilities as absent or partial even though
  implementation has moved forward.

Impact:

- The docs are no longer a reliable project status source.
- New contributors need to inspect code to know what is real.
- Planning from old docs can duplicate already-shipped work.

Recommended direction:

- Create a docs freshness pass after each feature PR.
- Keep `docs/STATUS.md` as the single current-state document.
- Treat roadmap docs as future intent only, not implementation inventory.
- Add a release checklist item: update README version and docs status.

## CI And Tooling Debt

### Web lint currently fails

Location: `web`

`npm run lint` fails because Prettier wants changes in:

- `web/src/lib/api/client.ts`
- `web/src/routes/[workspace]/clusters/+page.svelte`

Impact:

- The current web CI job fails.

Recommended action:

- Run `npm run format` in `web`, or format only the affected files.
- Re-run `npm run lint`.

### Landing lint command is broken

Location: `landing/package.json`

`npm run lint` runs `next lint`, which fails in the current Next 16 setup.

Impact:

- Landing lint cannot be used locally or in CI as written.
- Landing is not covered by the root CI workflow.

Recommended action:

- Replace `next lint` with an explicit ESLint setup, or remove lint until a
  working command exists.
- Add landing build/lint to CI if the landing is part of the shipped product.

### Postgres is not exercised by CI

Location: `.github/workflows/ci.yml`

The CI runs Python tests, web checks/build, and Playwright E2E, but does not
start a Postgres service.

Impact:

- Postgres hot paths can regress without default CI catching it.
- Only SQLite storage is consistently exercised.

Recommended action:

- Add a CI job with Postgres service.
- Set `SELFEVALS_TEST_POSTGRES_URL`.
- Run storage/query/API contract tests against Postgres.

## Additional Audit Findings

This section records the smell-focused pass over the repo. Mechanical counts
from the current working tree:

- `except Exception`: 42 occurrences in 19 files.
- `type: ignore`: 118 occurrences in 39 files.
- `json_extract`: 10 occurrences in 5 files.
- `list_entities(`: 66 occurrences in 28 files.
- `get_entity(`: 43 occurrences in 23 files.
- `X-SelfEvals-User`: 20 occurrences in 9 files.
- `SELFEVALS_`: 97 occurrences in 23 files.

These counts are not all bugs by themselves, but they show where contracts and
fallback behavior are spread across the codebase.

### Dataset API write paths are SQLite-only

Locations:

- `src/selfevals/api/app.py:376`
- `src/selfevals/api/dataset_writer.py:86`
- `src/selfevals/api/dataset_writer.py:176`

`create_dataset_from_request()` and `freeze_dataset()` are called from the API,
but the writer path instantiates `SQLiteStorage(db_path)` directly instead of
using the resolved storage backend.

Impact:

- With `SELFEVALS_STORAGE_URL=postgresql://...`, these endpoints try to treat a
  Postgres URL as a SQLite path.
- Postgres deployments can pass health checks but fail on dataset create/freeze
  workflows.
- The API already has a storage abstraction, but this path bypasses it.

Recommended action:

- Pass `StorageInterface` into dataset writer operations.
- Remove direct `SQLiteStorage` construction from API write paths.
- Add backend parity tests for dataset create/freeze against SQLite and
  Postgres.

### Multi-entity writes are not atomic

Locations:

- `src/selfevals/repo/datasets.py:202`
- `src/selfevals/repo/datasets.py:215`
- `src/selfevals/api/dataset_writer.py:340`
- `src/selfevals/api/dataset_writer.py:345`
- `src/selfevals/api/failure_mode_ops.py:102`

Dataset persistence writes multiple `EvalCase` entities and then the `Dataset`.
Appending a promoted case writes the case and then updates the dataset ref list,
manifest hash, and statistics. Failure-mode merge updates destination and
source separately.

Impact:

- A crash or storage error can leave orphaned cases.
- Dataset manifests can disagree with persisted cases.
- Failure-mode merges can duplicate examples without retiring the source.
- The absence of DB-level FKs means storage cannot catch these partial states.

Recommended action:

- Add transaction support to `StorageInterface`.
- Treat dataset create/append/freeze and failure-mode merge as atomic units.
- Add repair/audit commands to detect orphaned cases and mismatched manifests.

### Redis enqueue is outside the durable write boundary

Locations:

- `src/selfevals/api/run_launcher.py:81`
- `src/selfevals/api/run_launcher.py:97`
- `src/selfevals/api/run_launcher.py:101`

The experiment and `RunJob` are persisted first; Redis enqueue happens after the
storage scope is closed.

Impact:

- If enqueue fails after persistence, the API can leave a queued job in storage
  with no Redis stream message.
- Workers will not see the job unless another recovery mechanism finds and
  requeues it.
- The API caller receives an error after durable state has already changed.

Recommended action:

- Add an outbox table/pattern for run dispatch.
- Or make workers poll durable queued jobs as a fallback.
- Make launch responses distinguish "persisted but dispatch failed".

### Broad exception catches mask corruption as not found

Locations:

- `src/selfevals/api/queries.py:143`
- `src/selfevals/api/queries.py:257`
- `src/selfevals/api/queries.py:408`
- `src/selfevals/api/queries.py:1026`
- `src/selfevals/api/run_jobs.py:53`

Several API query helpers catch `Exception` and return `None`; handlers then
map that to 404 or empty UI states.

Impact:

- Storage errors, JSON decode errors, Pydantic validation errors, and workspace
  mismatches can look like missing resources.
- Data corruption becomes harder to detect.
- Frontend empty states can hide backend regressions.

Recommended action:

- Catch expected domain errors only, such as `EntityNotFoundError`.
- Let corruption, validation, and database errors surface as 500s with logs.
- Add tests that malformed persisted payloads do not become 404s.

### Orphaned dataset refs are silently hidden

Locations:

- `src/selfevals/api/queries.py:1019`
- `src/selfevals/api/queries.py:1029`
- `src/selfevals/api/queries.py:1048`

`dataset_detail()` skips missing `EvalCase` rows while still reporting
`case_count=len(ds.cases)`.

Impact:

- Dataset detail can return fewer cases than the reported count.
- Referential corruption is hidden from API consumers.
- Missing cases are not observable unless someone manually audits storage.

Recommended action:

- Return explicit missing refs in the response, or fail loudly for corrupted
  datasets.
- Add a `selfevals doctor` check for dangling dataset refs.

### Runner/spec boundary is too loose

Locations:

- `src/selfevals/repo/loader.py:267`
- `src/selfevals/runner/launch.py:395`
- `src/selfevals/runner/launch.py:446`
- `src/selfevals/runner/launch.py:858`
- `src/selfevals/graders/registry.py:116`

The loader validates YAML into loose `dict[str, Any]` params. The launcher then
reconstructs runtime graders and funnel objects from those dicts with casts.
Built-in grader specs are registered through a module-level global registry,
while dotted-path graders use a separate resolution path.

Impact:

- Loader and launcher must stay manually synchronized for each grader type.
- Invalid specs can pass one layer and fail later with lower-context runtime
  errors.
- Global temporary registry mutation is a brittle contract for concurrent or
  embedded usage.

Recommended action:

- Introduce typed intermediate config models for authored YAML.
- Make loader output those models instead of unstructured param dicts.
- Replace temporary global registration with an explicit resolver/context.

### Runtime components depend on private fields

Locations:

- `src/selfevals/runner/otlp_receiver.py:143`
- `src/selfevals/runner/otlp_receiver.py:192`
- `src/selfevals/runner/multiturn.py:102`

`_SpanIngest` stores the recorder as `Any` and reads private recorder fields via
`getattr`. `MultiTurnExecutor` calls private `Executor` fields and methods such
as `_agent_ref`, `_run_single`, and `_workspace_id`.

Impact:

- Internal refactors can break OTLP ingest and multi-turn execution without a
  type checker warning.
- There is no stable protocol for "record spans" or "run one turn".

Recommended action:

- Define typed protocols for recorder access and single-turn execution.
- Move shared execution behavior behind public methods.

### Infrastructure faults can become grader output

Locations:

- `src/selfevals/graders/llm_judge.py:148`
- `src/selfevals/graders/pairwise.py:143`
- `src/selfevals/runner/baseline.py:164`

Judge invocation failures are converted to `GradeLabel.ERROR`. Auto-baseline
write failures are logged and ignored.

Impact:

- Provider outages, adapter bugs, and auth/config errors can be mixed into
  model-quality metrics.
- Baseline protection can silently fail after a run.

Recommended action:

- Split expected grading failures from infrastructure failures.
- Track infra failures as run/job health signals.
- Surface baseline write failure in run metadata even if the run continues.

### Search-space and adapter contracts are under-typed

Locations:

- `src/selfevals/optimization/proposers.py:119`
- `src/selfevals/optimization/proposers.py:187`
- `src/selfevals/runner/adapters.py:384`
- `src/selfevals/schemas/experiment.py:103`
- `src/selfevals/schemas/iteration.py:67`
- `src/selfevals/schemas/eval_case.py:196`
- `src/selfevals/schemas/eval_case.py:203`

Search spaces, proposals, adapter responses, case inputs, and case context use
`dict[str, Any]` or broad coercion. `_sample_value()` accepts several ad hoc
shapes. Adapter response decoding coerces raw values with `str()`, `int()`,
`float()`, and `dict()`.

Impact:

- Invalid authoring shapes fail late.
- Some bad adapter responses can be silently coerced into empty/default values.
- Consumers cannot rely on schema-level discriminants.

Recommended action:

- Add discriminated Pydantic models for search-space dimensions.
- Validate adapter responses through a concrete response model.
- Preserve flexible case payloads, but provide stricter typed helpers for
  common task types.

### Frontend polling can overlap

Locations:

- `web/src/routes/[workspace]/experiments/[experiment]/+page.svelte:76`
- `web/src/routes/[workspace]/experiments/[experiment]/+page.svelte:152`
- `web/src/lib/components/ActiveRunsPill.svelte:24`

Experiment detail invalidates all data every 2.5 seconds, active runs poll every
2 seconds, and live-run lookup polls every 3 seconds. These loops do not guard
against an in-flight request.

Impact:

- Slow API responses can stack.
- A busy page can generate redundant backend load.
- Failure states are hard to reason about because new polls race old ones.

Recommended action:

- Add in-flight guards or use a single polling store.
- Prefer SSE/job state where available.
- Track and render polling errors instead of silently retrying forever.

### SSE error handling is fragile

Locations:

- `web/src/lib/api/sse.ts:36`
- `web/src/lib/api/sse.ts:45`
- `web/src/lib/api/sse.ts:54`
- `web/src/lib/api/sse.ts:66`
- `web/src/routes/[workspace]/traces/[trace]/+page.svelte:119`

Malformed SSE events are swallowed. Browser auto-reconnect continues unless the
caller closes the stream. The trace page does not pass an `onError` handler.

Impact:

- Bad event payloads disappear without telemetry.
- Trace pages can show stale live state after stream failures.

Recommended action:

- Surface malformed event counts in dev/test.
- Let callers configure retry/close behavior.
- Pass explicit error handlers from trace/experiment pages.

### Frontend route loaders erase backend failures

Locations:

- `web/src/routes/[workspace]/+page.server.ts:10`
- `web/src/routes/[workspace]/experiments/+page.server.ts:15`
- `web/src/routes/[workspace]/experiments/[experiment]/+page.server.ts:9`
- `web/src/routes/[workspace]/experiments/[experiment]/+page.server.ts:14`

Some SvelteKit loaders catch API errors and return empty arrays.

Impact:

- Backend regressions can look like "no anchors", "no datasets", or "no
  decisions".
- Contract failures become UI state bugs instead of actionable errors.

Recommended action:

- Only degrade optional panels, and include an explicit load error field.
- Keep primary route data failures as route errors.

### Direct frontend fetches bypass the client

Locations:

- `web/src/lib/api/client.ts:627`
- `web/src/lib/api/client.ts:771`
- `web/src/lib/components/ActiveRunsPill.svelte:11`

The API client hardcodes `X-SelfEvals-User: local`, payload resolution has a
separate fetch/error path, and `ActiveRunsPill` performs direct fetch with its
own header.

Impact:

- Auth changes require searching components, not just updating the client.
- Error parsing behavior can diverge endpoint by endpoint.

Recommended action:

- Centralize auth/header construction.
- Route payload resolution and active-runs fetches through the same client
  request helper.

### Browser storage assumptions are not guarded

Locations:

- `web/src/lib/stores/theme.ts:13`
- `web/src/lib/stores/theme.ts:37`
- `landing/src/lib/LangContext.tsx:16`
- `landing/src/lib/LangContext.tsx:26`

Theme and language persistence call `localStorage` without handling blocked
storage, private browsing restrictions, or quota failures.

Impact:

- Preference UI can throw in restricted browser environments.

Recommended action:

- Wrap localStorage reads/writes in small safe helpers.

### Landing terminal animation leaks timers

Locations:

- `landing/src/components/Terminal.tsx:70`
- `landing/src/components/Terminal.tsx:71`
- `landing/src/components/Terminal.tsx:90`
- `landing/src/components/Terminal.tsx:94`
- `landing/src/components/Terminal.tsx:101`
- `landing/src/components/Terminal.tsx:103`

The landing terminal schedules recursive `setTimeout` calls but cleanup only
disconnects the observer. The effect also suppresses `exhaustive-deps`.

Impact:

- Timers can fire after unmount.
- Lint suppression hides lifecycle dependency drift.

Recommended action:

- Track timeout ids and clear them on cleanup.
- Remove or narrowly justify the lint suppression.

### API and config docs are stale in specific contracts

Locations:

- `docs/api_reference.md:500`
- `src/selfevals/api/schemas.py:436`
- `src/selfevals/api/app.py:1326`
- `docs/eval_config.md:419`
- `src/selfevals/repo/loader.py:539`
- `src/selfevals/runner/launch.py:302`
- `docs/STATUS.md:148`
- `tests/api/test_pairwise.py:1`
- `README.md:25`
- `pyproject.toml:3`
- `src/selfevals/version.py:1`

Specific drift found:

- API reference documents thread responses with old `ThreadTurn` fields, while
  the schema now returns `ScenarioResult` turns.
- Eval config docs omit supported `pairwise` and `confusion` grader block
  types.
- Status docs say there is no pairwise comparison or multi-judge consensus,
  while pairwise endpoints/tests and judge-panel docs exist.
- README says current version `0.9.0`, while package/version files say
  `0.12.0`.

Impact:

- External clients can implement the wrong API contract.
- Users will miss supported grader types.
- Release/status docs cannot be trusted as source of truth.

Recommended action:

- Generate API reference from OpenAPI or add contract-check tests for docs.
- Sync grader docs from loader-supported types.
- Make `README.md` version dynamic or remove the stale hardcoded number.

### Local runtime docs reference a missing `.env.example`

Locations:

- `README.md:82`
- `docs/deploy.md:127`
- `.gitignore:57`

Docs tell users to use repository `.env` values, but `.env` is ignored and no
`.env.example` exists.

Impact:

- Fresh clones do not contain the documented local Postgres/Redis values.
- Local setup depends on private state outside the repo.

Recommended action:

- Add `.env.example` with non-secret local defaults.
- Update README/deploy docs to reference `.env.example`.

### Generated artifacts are mixed into repository state

Locations:

- `SELFEVALS_SCALE_ARCHITECTURE_ANALYSIS.html:1`
- `docs/feature-map.html:176`
- `objects/seals/...bin`

Generated HTML and binary artifacts are present in or near source-controlled
paths.

Impact:

- Review noise increases.
- It is unclear whether generated reports are source-of-truth docs or outputs.
- Binary artifacts can grow unnoticed.

Recommended action:

- Decide which artifacts are intentional fixtures.
- Move generated reports under an ignored output directory, or document them as
  maintained artifacts.

## Local Workspace State

During the audit, the working tree had uncommitted changes unrelated to this
documentation file. They were not modified by this documentation pass.

Observed changed/untracked areas included:

- `src/selfevals/api/schemas.py`
- `src/selfevals/repo/loader.py`
- `src/selfevals/runner/launch.py`
- `src/selfevals/schemas/__init__.py`
- `src/selfevals/schemas/eval_case.py`
- pairwise-related new files under `api`, `graders`, `runner`, `schemas`, and
  tests.

This matters because some findings refer to the current working tree, not only
the last committed state.

## Suggested Remediation Order

1. Fix immediate CI failures:
   - format web files
   - replace broken landing lint command
   - decide whether landing belongs in CI
2. Fix Postgres-breaking dataset API paths by removing direct `SQLiteStorage`
   construction from API writers.
3. Add Postgres and Redis CI coverage for production storage/queue paths.
4. Add transaction/outbox support for multi-entity writes and run dispatch.
5. Split `api/app.py` into routers and add storage dependency cleanup.
6. Split `web/src/lib/api/client.ts` and/or generate types from OpenAPI.
7. Split the experiment detail Svelte page into tab components and hooks.
8. Split `storage/postgres.py` into schema, storage, projections, metrics, and
   trace fact modules.
9. Add versioned Postgres migrations.
10. Add relational constraints/FKs for hot production entities.
11. Make optimistic concurrency atomic.
12. Replace ad hoc hot-method discovery with explicit query services.
13. Convert YAML loader validation to typed authoring config models.
14. Add real auth and workspace role enforcement before any shared deployment.
15. Fix frontend polling/SSE/error-state debt.
16. Run a docs freshness pass and make `docs/STATUS.md` the current truth.
