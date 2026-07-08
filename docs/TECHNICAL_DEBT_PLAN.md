# Technical debt closure plan

Date: 2026-06-28

Source audit: `docs/TECHNICAL_DEBT.md`

This plan turns the current debt inventory into executable work. The goal is to
close the known debt without destabilizing the product: each phase should land
as one or more small PRs with focused tests, and the technical-debt baseline
should only move downward unless a human explicitly accepts a new tradeoff.

## Success Criteria

The debt closure effort is complete when:

- `uv run python scripts/audit_technical_debt.py --fail-on-regression` passes
  without increasing `docs/quality/technical_debt_baseline.json`.
- Non-local API auth has a real principal provider and workspace authorization
  is enforced on read/write routes.
- Run dispatch cannot leave durable queued jobs invisible to workers.
- API, web client, experiment page, trace page, loader/launch, and trace mapper
  files are below the audit limits or have documented, accepted exceptions.
- Loader/launch boundaries use typed authoring/factory models for core grader,
  adapter, proposer, and dataset blocks.
- Runtime OTLP and multi-turn execution use public typed protocols instead of
  private field access.
- `docs/STATUS.md` and inline runtime comments match the Postgres-only
  architecture and package version.

## Execution Principles

- Close correctness and safety debt before cosmetic modularity.
- Prefer ratchets over rewrites: every touched area should reduce a measured
  count or move behavior behind a clearer contract.
- Do not update `docs/quality/technical_debt_baseline.json` unless a reviewer
  accepts the remaining finding as intentional.
- Keep API handlers thin: route modules should call dependencies and domain
  helpers, not own storage orchestration.
- Preserve the Postgres-only architecture. Do not reintroduce SQLite fallback,
  generic JSON entity persistence, or ad hoc storage capability checks.
- For frontend work, all network access must stay in the shared API layer.

## Phase 0: Stabilize The Audit Baseline

Status: completed on 2026-06-28 without updating
`docs/quality/technical_debt_baseline.json`.

Purpose: remove current measured regressions so the audit can serve as a useful
gate again.

Scope:

- Review `src/selfevals/runner/redis_throttle.py` broad exception catches.
- Review `tests/runner/test_redis_throttle.py`,
  `tests/runner/test_redis_throttle_integration.py`, and the current
  `tests/runner/test_launch_wiring.py` type-ignore increase.
- Replace broad catches with specific Redis/client errors where practical.
- Replace test ignores with typed fakes, casts, or protocol fixtures where
  practical.
- Re-run the audit and keep the baseline unchanged.

Acceptance:

- `broad_exception_catches <= 40`.
- `type_ignores <= 131`.
- `uv run python scripts/audit_technical_debt.py --fail-on-regression` passes.

Checks:

```bash
uv run ruff check .
uv run mypy src/selfevals
uv run python scripts/audit_technical_debt.py --fail-on-regression
```

## Phase 1: Harden Auth And Workspace Authorization

Status: completed for strict header-based auth on 2026-06-28. Workspace-scoped
API routes now enforce membership and coarse read/write roles outside local
mode. A signed token/session provider remains a later production hardening step.

Purpose: make the API safe for non-local deployments.

Scope:

- Extend `src/selfevals/api/auth.py` with a non-local principal resolver.
- Add authorization dependencies for workspace reads and mutations.
- Define role requirements for major actions:
  - read-only views
  - experiment launch/cancel
  - dataset create/upload/freeze
  - failure-mode and baseline mutations
  - membership/admin operations when those endpoints exist
- Apply dependencies route-by-route while keeping local mode compatible.
- Move web request auth/header construction into one helper.
- Remove the separate `resolvePayload()` header path in `web/src/lib/api/client.ts`.

Acceptance:

- Cross-workspace access is denied in tests.
- Mutating endpoints reject callers without the required role.
- Local mode remains frictionless for development.
- No component-level auth headers are introduced.

Checks:

```bash
uv run pytest tests/api
uv run ruff check .
uv run mypy src/selfevals
cd web && npm run lint && npm run check && npm run build
```

## Phase 2: Make Run Dispatch Durable

Status: completed on 2026-06-28 with durable queued-job recovery. Redis remains
the primary worker signal, but workers now poll Postgres `run_jobs` in `queued`
state when the stream is empty, and launch responses report `dispatch-pending`
when Redis dispatch fails after persistence.

Purpose: prevent Postgres and Redis from disagreeing about queued work.

Scope:

- Choose one dispatch strategy:
  - preferred: durable outbox table processed by the worker or a small sweeper
  - accepted fallback: workers poll durable queued jobs when Redis dispatch is
    missing
- Add a forward-only Postgres migration for the chosen state if needed.
- Make launch responses distinguish:
  - persisted and dispatched
  - persisted but dispatch pending
  - rejected before persistence
- Add tests for Redis outage during launch and worker recovery.
- Document operational behavior in deploy/troubleshooting docs.

Acceptance:

- A Redis outage after persistence does not lose work.
- Workers can recover or claim pending durable work.
- Launch API no longer reports an ambiguous failure after durable state changed.

Checks:

```bash
uv run pytest tests/api tests/worker tests/runner
uv run ruff check .
uv run mypy src/selfevals
uv run python scripts/audit_technical_debt.py --fail-on-regression
```

## Phase 3: Split The API Surface

Purpose: reduce `src/selfevals/api/app.py` from a route monolith into resource
modules without changing behavior.

Scope:

- Add shared dependencies for storage, object store, principal, and workspace
  authorization.
- Move routes into modules by resource:
  - workspaces
  - experiments and runs
  - iterations and decisions
  - datasets
  - failure modes and baselines
  - traces, payloads, threads, and streams
  - metrics and analysis
- Keep `build_app()` responsible for app construction, middleware, lifespan,
  static mounting, and router registration only.
- Split `src/selfevals/api/schemas.py` by resource after route modules are
  stable.

Acceptance:

- `src/selfevals/api/app.py` is below the Python large-file threshold.
- Existing API tests pass without route contract changes.
- New route modules have narrow ownership and no repeated storage cleanup.

Checks:

```bash
uv run pytest tests/api
uv run ruff check .
uv run mypy src/selfevals
```

## Phase 4: Split The Web API Client And Heavy Pages

Purpose: reduce frontend maintenance risk while preserving the current UX.

Scope:

- Split `web/src/lib/api/client.ts` into:
  - request/error/auth helper
  - shared generated or manual types
  - resource clients for workspaces, experiments, datasets, metrics, traces,
    pairwise, and runs
- Decide whether to generate types from OpenAPI. If generation is deferred,
  keep a single manual `types.ts` file and document the follow-up.
- Extract experiment detail into header, tabs, tab bodies, drawer, and polling
  hooks.
- Extract trace detail into header, span panel, detail panel, payload renderer,
  promotion modal, and stream hook.
- Add in-flight guards for polling loops and explicit SSE error reporting.

Acceptance:

- `web/src/lib/api/client.ts`,
  `web/src/routes/[workspace]/experiments/[experiment]/+page.svelte`, and
  `web/src/routes/[workspace]/traces/[trace]/+page.svelte` are below audit
  limits or have a reviewed exception.
- Web lint/check/build pass.
- No direct internal `fetch` is added outside the allowed API layer.

Checks:

```bash
cd web && npm run lint && npm run check && npm run build
uv run python scripts/audit_technical_debt.py --fail-on-regression
```

## Phase 5: Type The Loader And Launch Boundary

Purpose: make authored YAML validation and runtime construction explicit.

Scope:

- Add typed Pydantic authoring models for:
  - agent blocks
  - dataset sources
  - grader blocks
  - proposer/search-space blocks
  - run/parallelism blocks
- Map authoring models to canonical runtime schemas in focused mapper modules.
- Split grader factories by grader type.
- Split adapter, proposer, dataset, and workspace launch builders.
- Replace temporary/global grader registry mutation with an explicit resolver
  context.

Acceptance:

- New grader or adapter support no longer requires editing a giant central
  branch in both loader and launcher.
- Invalid YAML fails at load time with contextual errors.
- `src/selfevals/repo/loader.py` and `src/selfevals/runner/launch.py` trend
  below audit limits.

Checks:

```bash
uv run pytest tests/repo tests/runner tests/graders
uv run ruff check .
uv run mypy src/selfevals
```

## Phase 6: Split Postgres Trace Mapping And Reduce Generic Scans

Purpose: keep Postgres relational-canonical code maintainable as trace detail
grows.

Scope:

- Split `src/selfevals/storage/postgres/mappers/trace.py` into focused helpers
  for trace root rows, spans, LLM calls, tool calls, retrieval/memory facts,
  grader results, and links.
- Add contract tests around trace round-tripping before the split.
- Review API query paths that still use generic `list_entities()` for high-volume
  operations and move them to typed query methods where warranted.
- Keep generic storage methods for low-volume admin/test use.

Acceptance:

- `trace.py` is below the Python large-file threshold.
- Trace round-trip and query tests pass.
- No new `json_extract` usage is introduced.

Checks:

```bash
uv run pytest tests/storage tests/api tests/trace
uv run ruff check .
uv run mypy src/selfevals
uv run python scripts/audit_technical_debt.py --fail-on-regression
```

## Phase 7: Replace Private Runtime Access With Protocols

Purpose: make OTLP ingest and multi-turn execution robust to executor/recorder
refactors.

Scope:

- Define public typed protocols for span recording and single-turn execution.
- Move shared executor behavior behind public methods.
- Update OTLP receiver and multi-turn executor to depend on protocols.
- Add focused tests that exercise the public contracts.

Acceptance:

- Runtime components no longer read private executor/recorder fields.
- Mypy covers the protocol contracts.
- Existing runner and OTLP tests pass.

Checks:

```bash
uv run pytest tests/runner tests/trace
uv run ruff check .
uv run mypy src/selfevals
```

## Phase 8: Documentation Freshness Pass

Purpose: make docs reflect the implementation after the debt closure work lands.

Scope:

- Update `docs/STATUS.md` from `v0.12.0` to the current package version and
  current capabilities.
- Remove or rewrite SQLite-era comments in API/SSE/broker code.
- Sync API and eval config docs with current schemas and loader-supported
  grader types.
- Update `docs/TECHNICAL_DEBT.md` with closed items and final audit counts.
- Add a short release checklist item requiring a debt-audit run before handoff.

Acceptance:

- `docs_version_drift` remains 0.
- `docs/STATUS.md` is the current-state source of truth.
- Technical-debt inventory and this plan agree on what remains open.

Checks:

```bash
uv run python scripts/audit_technical_debt.py --fail-on-regression
uv run ruff check .
uv run mypy src/selfevals
```

## PR Slicing

Recommended sequence:

1. Audit regression cleanup for Redis throttle.
2. Auth dependency and route authorization tests.
3. Durable dispatch/outbox or worker polling fallback.
4. API route split, one resource group per PR.
5. API schemas split.
6. Web API client split.
7. Experiment page component split.
8. Trace page component split.
9. Loader authoring models.
10. Launch factory split.
11. Postgres trace mapper split.
12. Runtime protocols.
13. Docs freshness pass.

Avoid combining behavior changes with large moves unless the move is required to
make the behavior testable.

## Tracking

Use these measurable counters as the debt burndown:

| Counter | Current | Target |
| --- | ---: | ---: |
| `broad_exception_catches` | 40 | <= 40, then lower opportunistically |
| `type_ignores` | 131 | <= 131, then lower opportunistically |
| `large_files` | 16 | 0 or reviewed exceptions |
| `large_python_symbols` | 20 | 0 or reviewed exceptions |
| `direct_frontend_fetch` | 0 | 0 |
| `direct_user_header` | 0 | 0 |
| `docs_version_drift` | 0 | 0 |
| `json_extract` | 5 | no new usage; reduce where query paths move |

Each phase should update this table or `docs/TECHNICAL_DEBT.md` only after the
code changes land and the audit output is verified.
