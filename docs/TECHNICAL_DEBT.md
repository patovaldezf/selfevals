# selfevals technical debt audit

Date: 2026-07-08

This document records the current technical-debt inventory after the
`feat/split-api-surface` closure effort (see `docs/TECHNICAL_DEBT_PLAN.md`,
phases 1–8). It replaces the 2026-06-28 snapshot: most of what that report
flagged as open is now closed.

## Current State

- Postgres is the only storage backend; no generic `entities` table, no
  JSON-payload canonical store.
- Auth supports three modes (`local`, `header`, `token`): `token` mode is
  signed HMAC session tokens (`selfevals.api.tokens`), issued via
  `POST /api/auth/session` behind an operator secret. Production-grade
  identity is closed — `local`/`header` remain for dev/trusted-bridge use.
- `api/app.py` is a thin `build_app()` (143 lines) that wires middleware and
  registers 10 resource routers under `api/routes/`; `api/schemas.py` and
  `api/queries.py` are each a package split by resource with a barrel
  `__init__.py` re-export, so existing imports were unaffected.
- The web `client.ts` (1242 lines) is split into `api/http.ts` (the one
  request/auth/error layer), `api/types.ts` (hand-maintained mirrors), and
  `api/resources/*.ts` per domain; `client.ts` is now a ~25-line barrel.
  `npm run gen:api` generates `api/types.gen.ts` from the live OpenAPI schema
  (`scripts/export_openapi.py`), with `gen:api:check` gating CI against drift.
- `repo/loader.py` and `runner/launch.py` are each split into packages by
  concern (`models`/`agent`/`datasets`/`graders` for the loader;
  `adapters`/`graders`/`datasets` for launch), preserving the exact
  dict-based validation behavior — no Pydantic rewrite, which would have been
  a much larger-risk change for the same size/readability goal.
- `storage/postgres/mappers/trace.py`'s span read/write logic is split into
  `trace_spans_write.py`/`trace_spans_read.py` (one function per span kind);
  `TraceMapper` remains the single registered `EntityMapper[Trace]`.
- The one real cross-object private access (`MultiTurnExecutor` reaching into
  `Executor._run_single`/`_agent_ref`/`_workspace_id`) is now a public
  contract (`Executor.run_single`/`.agent_ref()`/`.workspace_id`).
- CLI's `_build_parser()` (738 lines) is split into `cli/parsers/*.py` by
  domain (meta, experiments, datasets, analysis, ops).
- `docs_version_drift` is 0: `pyproject.toml`, `docs/STATUS.md`, and this
  file agree on `0.13.0`. SQLite-era comments in `api/broker.py`,
  `api/sse.py`, `api/run_launcher.py` are gone.
- `mypy --strict` runs clean on `src/selfevals` (219 source files) and is a
  CI gate. Pytest coverage is now a CI gate too (`fail_under = 85`, measured
  ~90%; see `[tool.coverage]` in `pyproject.toml`).

## Verification Snapshot

```bash
uv run python scripts/audit_technical_debt.py --json
uv run ruff check .
uv run mypy src/selfevals
uv run pytest --cov=selfevals --cov-report=term-missing
cd web && npm run lint && npm run check && npm run build && npm run gen:api:check
cd landing && npm run lint && npm run build
docker compose up -d postgres redis && cd web && npm run test:e2e   # E2E (Playwright)
```

Audit counts (baseline updated 2026-07-08, after human review of this
closure effort):

| Counter | Was (06-28) | Now | Target |
| --- | ---: | ---: | --- |
| `broad_exception_catches` | 40 | 23 | keep ≤23, lower opportunistically |
| `type_ignores` | 131 | 99 | keep ≤99, lower opportunistically |
| `large_files` | 15 | **7** | 0 or reviewed exceptions |
| `large_python_symbols` | 20 | **18** | 0 or reviewed exceptions |
| `json_extract` | 5 | 5 | no new usage |
| `list_entities_calls` | 65 | 65 | prefer typed queries for hot paths |
| `get_entity_calls` | 51 | 52 | prefer typed queries for hot paths |
| `direct_frontend_fetch` | 0 | 0 | 0 |
| `direct_user_header` | 0 | 0 | 0 |
| `docs_version_drift` | 0 | 0 | 0 |

## Remaining Debt

Ranked by what would move the needle most if picked up next.

### 1. `large_files` — 7 remaining

- `src/selfevals/cli/commands.py` (764 lines) — the actual command handler
  bodies (as opposed to `cli/main.py`'s parser wiring, now split). Splitting
  this needs the same "which domain does each handler belong to" grouping
  used for `cli/parsers/`, but the handlers share more helper state.
- `tests/optimization/test_aggregator.py`, `tests/optimization/test_loop.py`,
  `tests/repo/test_loader.py`, `tests/runner/test_launch_wiring.py` — large
  test files. Split only when touching the module they test, to avoid
  churn-only test reshuffles.
- `web/src/routes/[workspace]/experiments/[experiment]/+page.svelte` (1387)
  and `.../traces/[trace]/+page.svelte` (968) — the two heavy Svelte pages.
  Explicitly deferred during the web split (Phase 3): extracting tab/panel
  components here needs a careful read of each page's reactive state, higher
  functional risk than the mechanical route/schema/CLI splits that shipped.
  Candidate components: experiment header/tabs/drawer/polling hook; trace
  header/span-panel/detail-panel/payload-renderer/promotion-modal/stream hook.

### 2. `large_python_symbols` — 18 remaining

Mostly single cohesive classes with real shared state (not mechanically
splittable the way the CLI parser or trace-span read/write were):

- `optimization/loop.py::OptimizationLoop` (496) + `_run_iterations` (136) —
  the core loop; every method reads/writes `self.*` run state.
- `runner/executor.py::Executor` (358), `runner/multiturn.py::MultiTurnExecutor`
  (187) + `run_case` (177), `runner/simulator.py::UserSimulator` (185) —
  same shape: cohesive runtime classes.
- `trace/recorder.py::TraceRecorder` (431) — one recorder, one trace's worth
  of accumulated state.
- `storage/postgres/mappers/{experiment,eval_case,iteration_record}.py` and
  `trace.py::TraceMapper` (post-split, 225) — each mapper is one
  `EntityMapper` registered once; splitting further means either sub-classing
  (adds indirection) or the free-function-delegate pattern already used for
  `trace.py`'s spans (viable follow-up for `experiment.py`'s `upsert`/`_build`,
  which are 125/175 lines).
- `storage/postgres/storage.py::PostgresStorage` (217) — the `StorageInterface`
  implementation; one class by design.
- `graders/judge_panel.py::JudgePanelGrader` (405), `graders/trajectory.py::
  TrajectoryGrader` (249), `graders/deterministic.py::grade` (142) — grader
  logic; likely splittable into prompt-building/aggregation helpers without
  changing the registry contract.
- `tests/schemas/test_cross_entity.py::test_end_to_end_chain_constructable`
  (193) — one big assertion chain; split by entity when next touched.

None of these are correctness or safety debt — they're readability/size debt
against the audit's mechanical thresholds. Pick up opportunistically when
touching the file for a real change, per the plan's "ratchet, not rewrite"
principle.

### 3. `tests/` is not yet under `mypy --strict`

A first `--strict` pass over `tests/` found ~280 errors across 37 files —
fixtures typed as `BaseEntity`/`object` instead of the concrete entity,
`**dict[str, object]` construction of Pydantic models, a few genuinely stale
`# type: ignore` comments. These are gaps in the tests' own typing, not
production bugs, and not fixable by loosening `strict` globally (tried;
even a heavily relaxed per-module override for `tests.*` still left ~280
real errors). `mypy` `files` is `["src/selfevals"]` only for now. Re-add
`tests` once a dedicated pass fixes these — do not add it back with a
loosened `strict` config, that defeats the point.

### 4. Auth identity strength

`token` mode (HMAC-signed sessions) is the production-ready path, but no
built-in rotation/revocation exists yet — a compromised secret invalidates
every issued token only by rotating `SELFEVALS_AUTH_SECRET` (which also
invalidates all outstanding sessions). Acceptable for a single-service
internal bridge; revisit if this becomes a multi-tenant public deployment.

### 5. Generic storage APIs still widely used

`list_entities()`/`get_entity()` remain the public storage contract
(`list_entities_calls: 65`, `get_entity_calls: 52`) — not automatically bad
(admin/tests use them intentionally), but query-heavy API paths should keep
preferring the typed hot-query methods in `storage/postgres/queries.py`
where the volume justifies it.

## Resolved Since 2026-06-28

- Auth: token mode + workspace-role authorization landed (was: header-only).
- Run dispatch durability: `dispatch-pending` + worker recovery from
  Postgres `run_jobs` landed (was: ambiguous failure on Redis outage).
- `api/app.py`, `api/schemas.py`, `api/queries.py` monoliths split.
- `web/src/lib/api/client.ts` monolith split; OpenAPI codegen added.
- `repo/loader.py`, `runner/launch.py` monoliths split.
- `storage/postgres/mappers/trace.py` span logic split out.
- `MultiTurnExecutor`/`Executor` private cross-object access closed.
- `cli/main.py`'s `_build_parser()` split into `cli/parsers/*.py`.
- Coverage gate added (`fail_under=85`, CI-enforced).
- Docs freshness: SQLite-era comments removed from `api/broker.py`,
  `api/sse.py`, `api/run_launcher.py`; `HealthResponse.storage_backend`
  default fixed (`"sqlite"` → `"postgres"`).
- Cosmetic: `Tooltip.svelte` timer cleanup on destroy, `InstallPill.tsx`
  (landing) timer cleanup on unmount.
