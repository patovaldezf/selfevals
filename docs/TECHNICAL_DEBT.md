# selfevals technical debt audit

Date: 2026-07-09

This document records the current technical-debt inventory after the
`feat/split-api-surface` closure effort (see `docs/TECHNICAL_DEBT_PLAN.md`,
phases 1–8) and the Feature Arena merge. It replaces the 2026-06-28
snapshot: most of what that report flagged as open is now closed.

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
- `mypy --strict` runs clean on `src/selfevals` and `tests/` (406 source
  files combined, post-Arena) and is a CI gate over both. Pytest coverage is
  now a CI gate too (`fail_under = 85`, measured ~90%; see `[tool.coverage]`
  in `pyproject.toml`).

## Verification Snapshot

```bash
uv run python scripts/audit_technical_debt.py --json
uv run ruff check .
uv run mypy src/selfevals tests
uv run pytest --cov=selfevals --cov-report=term-missing
cd web && npm run lint && npm run check && npm run build && npm run gen:api:check
cd landing && npm run lint && npm run build
docker compose up -d postgres redis && cd web && npm run test:e2e   # E2E (Playwright)
```

Audit counts (baseline updated 2026-07-09, after human review of the
Feature Arena merge — `get_entity_calls`/`list_entities_calls` rose from
legitimate queries against the new `Arena`/`ArenaRound`/`ArenaVariant`
entities, not from regressed hot-path code):

| Counter | Was (06-28) | Now | Target |
| --- | ---: | ---: | --- |
| `broad_exception_catches` | 40 | 23 | keep ≤23, lower opportunistically |
| `type_ignores` | 131 | 86 | keep ≤86, lower opportunistically |
| `large_files` | 15 | **4** | 0 or reviewed exceptions |
| `large_python_symbols` | 20 | **8** | 0 or reviewed exceptions |
| `json_extract` | 5 | 5 | no new usage |
| `list_entities_calls` | 65 | 85 | prefer typed queries for hot paths |
| `get_entity_calls` | 51 | 81 | prefer typed queries for hot paths |
| `direct_frontend_fetch` | 0 | 0 | 0 |
| `direct_user_header` | 0 | 0 | 0 |
| `docs_version_drift` | 0 | 0 | 0 |

## Remaining Debt

Ranked by what would move the needle most if picked up next.

### 1. `large_files` — 4 remaining

- `tests/optimization/test_aggregator.py`, `tests/optimization/test_loop.py`,
  `tests/repo/test_loader.py`, `tests/runner/test_launch_wiring.py` — large
  test files. Split only when touching the module they test, to avoid
  churn-only test reshuffles.

`cli/commands.py` (split into domain handlers) and both heavy Svelte pages
(`experiments/[experiment]/+page.svelte` 1387→197,
`traces/[trace]/+page.svelte` 968→117) are resolved — see "Resolved Since
2026-06-28".

### 2. `large_python_symbols` — 8 remaining

Single cohesive classes with real shared state (not mechanically splittable
the way the CLI parser or trace-span read/write were), plus two new
mechanical entries from Feature Arena (parser/route registration functions —
same shape as the pre-split `cli/main.py`, splittable the same way if this
domain grows further):

- `optimization/loop.py::OptimizationLoop` (425) + `_run_iterations` (131) —
  the core loop; every method reads/writes `self.*` run state.
- `runner/executor.py::Executor` (201) — cohesive runtime class.
- `storage/postgres/storage.py::PostgresStorage` (217) — the `StorageInterface`
  implementation; one class by design.
- `trace/recorder.py::TraceRecorder` (392) — one recorder, one trace's worth
  of accumulated state.
- `graders/judge_panel.py::JudgePanelGrader` (210) — grader logic; likely
  splittable into prompt-building/aggregation helpers without changing the
  registry contract.
- `api/routes/arena.py::register` (244) — one function registering all
  Arena HTTP endpoints; splittable by sub-resource if it grows.
- `cli/parsers/arena.py::add_arena` (227) — one function wiring all `arena`
  subcommands; same shape as the other `cli/parsers/*.py` modules.

None of these are correctness or safety debt — they're readability/size debt
against the audit's mechanical thresholds. Pick up opportunistically when
touching the file for a real change, per the plan's "ratchet, not rewrite"
principle.

### 3. `tests/` is now under `mypy --strict` (resolved)

The first `--strict` pass over `tests/` found ~280 errors across 37 files —
fixtures typed as `BaseEntity`/`object` instead of the concrete entity,
narrow `Literal` params on test helpers, untyped generator fixtures, and a
handful of genuinely stale `# type: ignore` comments. All fixed without
loosening `strict` anywhere; `mypy` `files` is now
`["src/selfevals", "tests"]` and both are a clean, CI-gated pass.

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
- `cli/commands.py` split by domain into `cli/commands/*.py`.
- Postgres mapper split (`experiment`/`eval_case`/`iteration_record`) applied
  the free-function-delegate pattern already used for `trace.py`'s spans.
- `runner`/`trace` symbol reduction: `Executor`/`MultiTurnExecutor`/
  `UserSimulator`/`TraceMapper`/`TraceRecorder` split into smaller units.
- `optimization`/`graders` split: `OptimizationLoop`, `JudgePanelGrader`,
  `TrajectoryGrader`, `deterministic.grade` all reduced.
- Both heavy Svelte pages extracted into components: experiment page
  1387→197 lines, trace page 967→117 lines.
- `tests/` brought under `mypy --strict` (see "Resolved" note above);
  `mypy` `files` now covers both `src/selfevals` and `tests`.
- Docs freshness: SQLite-era comments removed from `api/broker.py`,
  `api/sse.py`, `api/run_launcher.py`; `HealthResponse.storage_backend`
  default fixed (`"sqlite"` → `"postgres"`).
- Cosmetic: `Tooltip.svelte` timer cleanup on destroy, `InstallPill.tsx`
  (landing) timer cleanup on unmount.
