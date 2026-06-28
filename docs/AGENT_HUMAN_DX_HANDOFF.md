# Agent ↔ Human DX — handoff

> Snapshot for a future session. The collaboration loop between a **coding agent**
> (operates selfevals via CLI/API) and a **human** (reviews and gates via the web)
> is operational end-to-end today. This doc maps what's ready and lists the
> remaining rough edges as discrete, scoped tasks — each with the files to touch
> and a definition of done — so the next session can close them. Verify each claim
> against the code before acting (repo rule); line numbers drift.

## The loop, in one picture

```
AGENT (CLI / API)                         HUMAN (web)
  run experiment ───────────────────────▶ sees iterations, funnel, decisions
  analyze pull → code → analyze push ────▶ /[ws]/failure-modes?status=candidate
                                           clicks Promote  ──┐
  next analyze pull (incl. new mode) ◀─────────────────────┘
  regression check (exit 0/1/2) ─────────▶ CI gate
```

This canonical flow (error-analysis → promote) is **100% operational** across
CLI, API, and web. The gaps below are quality-of-life, not blockers.

## Capability map (what's ready)

| Capability                | CLI                         | API                              | Web                          | Ready? |
| ------------------------- | --------------------------- | -------------------------------- | ---------------------------- | ------ |
| Create workspace          | `init`                      | `POST /workspaces`               | —                            | ✅ |
| Create / freeze dataset   | `dataset create/freeze`     | `POST /datasets`, `/freeze`      | datasets page (create/freeze)| ✅ |
| Launch run                | `run`                       | `POST /experiments/run` (202)    | run button                   | ✅ (async + poll) |
| Inspect iterations        | `iteration list`, `report`  | `GET …/iterations`               | Iterations tab               | ✅ |
| Compare iterations        | `compare`                   | `GET …/compare`                  | Compare tab                  | ✅ (CLI: no JSON — gap 1) |
| Error analysis            | `analyze pull/push`         | `GET …/analysis/bundle`, `POST …/ingest` | Analyze tab          | ✅ |
| Promote / retire / merge / edit mode | `failuremode …` | `POST …/promote` etc., `PATCH …` | failure-modes page           | ✅ (CLI: no JSON — gap 1) |
| Baseline                  | `baseline show/set`         | `PUT …/baseline`                 | (via API)                    | ✅ (CLI: no JSON — gap 1) |
| Regression gate (CI)      | `regression check` (0/1/2)  | `POST …/regression-check`        | (via API)                    | ✅ |
| Pairwise tournament       | `demo` only                 | `POST …/tournaments`, `/verdicts/ingest` | Pairwise tab         | ✅ |
| Trace / thread live view  | —                           | `GET …/traces/{id}/stream` (SSE) | trace + thread viewers       | ✅ |
| Failure clusters          | —                           | `GET …/clusters`                 | **stub FE** — gap 3          | ⚠️ |
| Decision override         | —                           | —                                | —                            | ❌ gap 4 |

## Gaps to close (prioritized)

### Gap 1 — `--format json` on CLI mutations (highest leverage for agents)

**Problem.** The mutation commands print only a human summary (e.g. `promoted
fm_… (slug) → official`). An agent has to regex the prose or trust exit 0; it
can't read back the updated entity. This is the single biggest friction for an
agent driving the loop by CLI.

**Scope.** Add `--format {text,json}` (default `text`) to: `failuremode
promote/retire/merge/edit`, `baseline set` (and `baseline show`), `analyze push`,
and `regression check`. On `json`, print the updated entity / result as the same
shape the API returns (reuse the API response models / mappers so there's one
serialization, not two).

**Files.** `src/selfevals/cli/analyze_commands.py` (failuremode + analyze push),
`src/selfevals/cli/baseline_commands.py` (baseline + regression),
`src/selfevals/cli/main.py` (add the flags). Mirror the existing `--format`
handling in `run`/`report` (`commands.py`, `reporter/`).

**Done when.** Each command with `--format json` emits valid JSON of the updated
entity/result to stdout; the human `text` output is unchanged as default; a test
asserts the JSON shape for at least `failuremode promote` and `regression check`.

### Gap 2 — Run-state stream (kill the polling boilerplate)

**Problem.** `POST …/experiments/run` returns 202; an agent then polls
`GET …/experiments/{exp}` for `state == completed`. Span-level SSE exists
(`…/traces/{run_id}/stream`) but there is no **run-state** stream, so the agent
busy-polls.

**Scope.** Either (a) add a run-state SSE/stream that emits
`queued/running/completed/failed` transitions, or (b) document the polling
contract crisply (endpoint, fields, recommended interval, terminal states) and
add a `--wait` flag to a CLI launch path that blocks until terminal. (b) is the
smaller win and may be enough.

**Files.** `src/selfevals/api/app.py` (`/experiments/run`, `/runs/active`, the SSE
machinery in `api/sse.py`/`broker.py`), and the CLI launch path if adding
`--wait`.

**Done when.** An agent can learn a run reached a terminal state without a fixed
polling loop — via a stream event or a documented blocking call — and the
terminal states (`completed`/`failed`/cancelled) are all observable.

### Gap 3 — Failure-clusters frontend

**Problem.** `GET …/clusters` (grouped-by-failure-mode) returns real data, but the
web route is a placeholder, so a human can't browse clusters.

**Scope.** Build the `/[ws]/clusters` page against the existing API: list clusters,
drill into the traces in each, link out to the trace viewer. Follow the
frontend-change conventions (shared API client, route-level error handling).

**Files.** `web/src/routes/[workspace]/clusters/` (page + loader),
`web/src/lib/api/` (client method if missing). API is already there in
`src/selfevals/api/app.py`.

**Done when.** The clusters route renders real clusters with drill-down to traces;
a Playwright test covers the happy path.

### Gap 4 — Decision override (lower priority)

**Problem.** The engine records a `DecisionOutcome` per iteration; a human can see
it (Decisions tab) but can't override it ("keep this despite the auto-reject").
No CLI command and no API endpoint.

**Scope.** Add an endpoint (`POST …/iterations/{itr}/decision`) + a CLI command to
record a human decision override with a rationale, and surface it in the
Decisions tab as a distinct human-authored record (don't overwrite the engine's —
append, audit-trail style). Note: the main error-analysis loop does **not** need
this; it's a power-user gate.

**Files.** `src/selfevals/api/app.py` (+ schema in `api/schemas.py`), a CLI
command (`cli/commands.py` + `cli/main.py`), the Decisions tab in `web/`.

**Done when.** A human can override an iteration's decision with a rationale via
API/CLI, the override is persisted as an audit record alongside the engine's, and
the web shows both.

### Minor / nice-to-have

- **SSE on `analyze push`** so the web's candidate list refreshes live instead of
  needing a manual reload after the agent pushes.
- **Workspace snapshot export** for an agent to work offline and re-apply (today
  everything requires a live DB / API). Low priority.

## Notes for the next session

- The capability map above was built from a read of `src/selfevals/cli/`,
  `src/selfevals/api/app.py`, and `web/src/routes/`. Re-verify before editing.
- Prefer one serialization: when adding CLI `--format json` (gap 1), reuse the API
  response models rather than hand-rolling dicts.
- Keep handlers thin and respect workspace isolation (see the
  `selfevals-api-change` skill) and the frontend client/error conventions (see
  `selfevals-frontend-change`).
