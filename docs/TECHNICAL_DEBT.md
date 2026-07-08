# selfevals technical debt audit

Date: 2026-06-28

This document records the current technical-debt inventory after verifying the
old debt report against the source tree. It intentionally removes historical
items that were closed by the Postgres-only migration, so the remaining list can
be used for planning instead of archaeology.

Remediation plan: `docs/TECHNICAL_DEBT_PLAN.md`.

## Current State

The large storage migration is complete:

- Postgres is the only storage backend (`src/selfevals/storage/factory.py`).
- There is no generic `entities` table or JSON-payload canonical store.
- Postgres schema changes are forward-only migrations under
  `src/selfevals/storage/postgres/migrations/`.
- Main entities have typed mappers under `src/selfevals/storage/postgres/mappers/`.
- Migrations now include foreign keys, check constraints, uniqueness constraints,
  and `_pg_migrations`.
- API dataset writes use injected storage instead of constructing SQLite directly.
- Hot query and metric methods are part of the storage contract instead of
  ad hoc capability checks.
- API storage cleanup uses a FastAPI `yield` dependency.
- `.env.example` exists.

The repo still has meaningful debt, but it is now mostly about modularity,
production-grade identity, typed boundaries, queue reliability, and frontend
maintainability.

## Verification Snapshot

Commands run during this pass:

- `uv run python scripts/audit_technical_debt.py --json`
- `rg` checks for stale SQLite/entity/auth/fetch references
- `wc -l` on known large files
- source review of Postgres migrations, storage factory, API auth, and web API
  client

Audit counts from the current working tree:

- `broad_exception_catches`: 38
- `type_ignores`: 131
- `json_extract`: 5
- `list_entities_calls`: 65
- `get_entity_calls`: 51
- `direct_frontend_fetch`: 0
- `direct_user_header`: 0
- `docs_version_drift`: 0
- `large_files`: 16
- `large_python_symbols`: 20

The current tree is below the tracked baseline for broad exception catches and
back at baseline for type ignores. The Redis throttle regressions were fixed
without updating `docs/quality/technical_debt_baseline.json`.

## Resolved Historical Debt

These items were still described as active in the old report, but source review
shows they are resolved:

- SQLite generic entity persistence: resolved by Postgres-only storage.
- Postgres projection-as-source-of-truth drift: resolved by typed relational
  mappers and removal of the JSON canonical entity table.
- Missing Postgres migration runner: resolved by
  `storage/postgres/migrations/__init__.py`.
- Missing FKs/checks/unique constraints for core tables: resolved across the
  Postgres migrations.
- Dataset API write paths using SQLite directly: resolved.
- API hot-method fallback discovery: resolved at the storage interface level.
- Missing `.env.example`: resolved.
- README version drift noted in the old debt report: resolved; README and
  `pyproject.toml` now say `0.13.0`.

## Highest Priority Debt

### 1. Auth still needs a production identity provider

Locations:

- `src/selfevals/api/auth.py`
- `src/selfevals/api/app.py`
- `web/src/lib/api/client.ts`

The auth seam is centralized, and non-local `SELFEVALS_AUTH_MODE` now enforces
workspace membership/role checks for workspace-scoped API routes. Local mode
still defaults to `local` for development. The remaining gap is identity
strength: strict mode still resolves the principal from `X-SelfEvals-User`,
which is suitable for a trusted internal bridge but not a public deployment.

Impact:

- Local mode remains convenient for development.
- Strict header mode now denies missing users, non-members, and read-only
  members attempting mutations.
- Caller identity is still header-controlled until a token/session provider is
  wired in.

Recommended direction:

- Replace strict header identity with a signed token/session provider before any
  untrusted shared deployment.
- Refine endpoint-specific role requirements after the API router split.
- Move frontend identity from the hardcoded local user to the eventual session
  mechanism.

### 2. Run dispatch has durable recovery but still depends on polling

Locations:

- `src/selfevals/api/run_launcher.py`
- `src/selfevals/api/run_queue.py`
- `src/selfevals/worker/runs.py`

The launch path persists experiment/run-job state and then dispatches to Redis.
If Redis dispatch fails after durable state is written, the API now returns
`dispatch-pending` instead of an ambiguous failure, and workers poll durable
`queued` run jobs when the Redis stream is empty.

Impact:

- A Redis outage after persistence no longer loses durable work.
- Recovery depends on a running worker reaching its durable poll path.
- Operational status is still split between Postgres and Redis.
- Entity writes with child tables are now atomic, so readers no longer observe
  transient main-row/child-row splits during experiment updates.

Recommended direction:

- Keep the durable poll path covered by tests while the Redis worker is used.
- Consider a formal outbox/sweeper if polling latency or duplicate stream
  messages become operationally noisy.
- Document worker recovery expectations in deploy/troubleshooting docs.

### 3. Broad exception handling can hide real faults

Locations include:

- `src/selfevals/api/dataset_writer.py`
- `src/selfevals/api/run_launcher.py`
- `src/selfevals/api/run_queue.py`
- `src/selfevals/repo/loader.py`
- `src/selfevals/runner/launch.py`
- `src/selfevals/runner/otlp_receiver.py`
- `src/selfevals/trace/recorder.py`
- current local addition: `src/selfevals/runner/redis_throttle.py`

The mechanical audit now finds 40 `except Exception` catches. Some are valid
isolation boundaries, but several sit near API, launch, loader, and telemetry
paths where corruption, validation errors, or infrastructure failures should not
be converted into empty states or generic failures.

Recommended direction:

- Catch expected domain errors explicitly.
- Let validation, storage, and corruption failures surface as 500s with logs.
- Keep best-effort telemetry catches, but annotate why swallowing is safe.
- Keep the count at or below baseline; do not introduce new broad catches without
  narrowing or documenting the failure boundary.

### 4. Central files are still too large

Current large production files:

- `src/selfevals/api/app.py`: 1538 lines.
- `src/selfevals/repo/loader.py`: 1120 lines.
- `src/selfevals/runner/launch.py`: 978 lines.
- `src/selfevals/api/schemas.py`: 988 lines.
- `src/selfevals/api/queries.py`: 905 lines.
- `src/selfevals/storage/postgres/mappers/trace.py`: 802 lines.
- `web/src/lib/api/client.ts`: 1237 lines.
- `web/src/routes/[workspace]/experiments/[experiment]/+page.svelte`: 1386 lines.
- `web/src/routes/[workspace]/traces/[trace]/+page.svelte`: 967 lines.

Recommended direction:

- Split `api/app.py` into resource routers plus shared dependencies.
- Split API schemas by resource.
- Move loader authoring parsing toward typed per-section models.
- Move launch wiring into adapter, proposer, grader, and dataset builder modules.
- Split trace mapping into span/result/link sub-mappers.
- Split web API methods by resource after centralizing request/auth behavior.
- Split experiment and trace Svelte pages into tab/panel components and polling
  hooks.

### 5. Loader and launch boundaries remain under-typed

Locations:

- `src/selfevals/repo/loader.py`
- `src/selfevals/runner/launch.py`
- `src/selfevals/graders/registry.py`

YAML authoring still flows through broad dictionaries for several grader,
adapter, proposer, and dataset parameters. The launcher then reconstructs
runtime objects with casts and branch-specific validation.

Impact:

- Loader and launcher must stay manually synchronized.
- Invalid specs can pass one layer and fail later with less context.
- Adding graders or adapters grows central branch-heavy files.

Recommended direction:

- Introduce Pydantic authoring models for YAML blocks.
- Map typed authoring models to canonical runtime schemas.
- Split grader-specific parsing/factory code by grader type.
- Replace temporary/global registry mutation with explicit resolver context.

### 6. Generic storage APIs are still widely used

Audit counts:

- `list_entities(`: 65
- `get_entity(`: 51
- `json_extract`: 5

Generic storage access is still part of the public storage contract, so these
are not automatically bugs. The debt is that query-heavy paths can drift back
toward broad entity scans instead of typed repository/query methods.

Recommended direction:

- Keep generic methods for low-volume admin and tests.
- Prefer typed query methods for API list/detail/metrics paths.
- Add audit coverage for new `json_extract` usage and large scan-heavy API
  additions.

### 7. Frontend API client and route pages are maintenance bottlenecks

Locations:

- `web/src/lib/api/client.ts`
- `web/src/routes/[workspace]/experiments/[experiment]/+page.svelte`
- `web/src/routes/[workspace]/traces/[trace]/+page.svelte`
- `web/src/lib/api/sse.ts`

The audit no longer finds direct component-level `fetch` calls outside the
allowed API layer, which is good. The remaining issue is concentration: the
client manually mirrors many Pydantic schemas and endpoint methods, while the
experiment/trace pages combine data loading, polling, SSE, tabs, modals, and
formatting helpers.

Recommended direction:

- Generate TypeScript API types from OpenAPI, or split manual types by resource.
- Keep one request/auth/error layer.
- Extract experiment tabs and trace panels into focused components.
- Add in-flight guards or shared stores for polling.
- Make SSE parse errors and stream failures visible in development/test.

### 8. Runtime internals depend on private fields

Locations:

- `src/selfevals/runner/otlp_receiver.py`
- `src/selfevals/runner/multiturn.py`
- `src/selfevals/runner/executor.py`

Some runtime components call private executor/recorder fields or use `Any` to
bridge around missing protocols.

Impact:

- Internal refactors can break OTLP ingest or multi-turn execution without type
  checker coverage.
- There is no stable public contract for "record this span" or "run one turn".

Recommended direction:

- Define typed protocols for recorder and single-turn execution behavior.
- Move shared execution behavior behind public methods.
- Add focused tests around the public protocol instead of private attributes.

### 9. Documentation status is partly stale

Locations:

- `docs/STATUS.md`
- docs that still mention SQLite-era implementation details
- inline comments in API/SSE/broker code that still say SQLite

The technical-debt document is now current, but other docs/comments still lag
the 0.13.0 Postgres-only architecture. `docs/STATUS.md` is headed `v0.12.0`
while `pyproject.toml` and `README.md` say `0.13.0`.

Recommended direction:

- Run a focused docs freshness pass after the current code changes settle.
- Update status/version references and remove SQLite-era comments where they no
  longer describe reality.
- Keep `docs/STATUS.md` as the current-state source of truth.

## Additional Audit Buckets

### Type ignores

The audit finds 131 `# type: ignore` occurrences. Many are in tests, provider
edge cases, and dynamic registry paths. Treat this as a ratchet: do not increase
the count casually, and remove ignores when touching nearby code.

### Large tests

Large test files still slow navigation:

- `tests/repo/test_loader.py`: 839 lines.
- `tests/runner/test_launch_wiring.py`: 827 lines.
- `tests/optimization/test_loop.py`: 791 lines.
- `tests/optimization/test_aggregator.py`: 730 lines.

Split only when changing nearby behavior; avoid churn-only test reshuffles.

### Browser storage and landing lifecycle

The previous audit called out unguarded `localStorage` usage and landing timer
cleanup. These were not re-audited in depth during this pass, so keep them as
lower-priority cleanup candidates rather than confirmed release blockers.

## Suggested Remediation Order

1. Add a durable outbox or worker polling fallback for Redis dispatch.
2. Split `api/app.py` into routers and move repeated route dependencies out of
   the app factory.
3. Split `web/src/lib/api/client.ts` and centralize payload/auth handling.
4. Extract experiment detail and trace pages into focused components/hooks.
5. Introduce typed YAML authoring models and per-grader launch factories.
6. Split `storage/postgres/mappers/trace.py` by trace child-table concerns.
7. Replace private runtime field access with typed protocols.
8. Replace strict header identity with a token/session provider.
9. Run a docs freshness pass for `docs/STATUS.md`, comments, and SQLite-era
    references.

## Local Workspace State

The working tree had unrelated local changes before this documentation update.
They were not modified except for this file. Observed changed/untracked areas
included runner launch/throttle/worker code and sentiment example files. The
current audit counts therefore reflect the working tree, not necessarily the
last committed state.
