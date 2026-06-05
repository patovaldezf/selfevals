# HTTP API Reference

The canonical reference for the selfevals HTTP bridge — a read-mostly
FastAPI service over the SQLite store (`src/selfevals/api/app.py`). Every
endpoint maps roughly 1:1 to a page in the web UI, and every response shape
is a Pydantic model in `src/selfevals/api/schemas.py`.

For the frontend architecture and the roadmap of _future_ endpoints, see
[`FRONTEND.md`](FRONTEND.md). For the YAML that drives experiments, see
[`eval_config.md`](eval_config.md). For the `report --format json` shape,
see [`json_report_schema.md`](json_report_schema.md).

## Conventions

- **Base path:** all endpoints are mounted under `/api` (no version prefix —
  this is a single internal service).
- **Auth:** stubbed via a single request header, `X-SelfEvals-User`
  (default `"local"`). Real auth is post-MVP; the header travels on every
  request so auth can be wired in later without touching any handler. It is
  only consumed today by `POST /api/workspaces` (as the new workspace's
  `owner_id`); other handlers accept and ignore it.
- **CORS:** allowed origins are `http://localhost:5173` and
  `http://127.0.0.1:5173`; methods `GET`, `POST`, `OPTIONS`.
- **OpenAPI:** the generated schema is served at `/api/openapi.json`, and
  interactive Swagger docs at `/api/docs`.
- **DB path:** resolved from the `--db` CLI flag / `db_path` build arg,
  falling back to the `SELFEVALS_DB` env var, then `./selfevals.sqlite`.
- **Errors:** `404` for an unknown entity, `400` for a malformed request
  (e.g. a cross-experiment compare or an invalid object-store pointer),
  `500` for a stored-payload hash mismatch (corruption).

## Running the API

```bash
python -m selfevals.api --host 127.0.0.1 --port 8000 --db ./selfevals.sqlite
```

`--reload` enables autoreload; `SELFEVALS_DB` is honored as a fallback for
`--db`.

---

## Meta

### `GET /api/health`

Liveness probe.

**Response** (`HealthResponse`):

| Field     | Type   | Notes                                        |
| --------- | ------ | -------------------------------------------- |
| `status`  | string | Always `"ok"`.                               |
| `db_path` | string | The resolved SQLite path the app is serving. |

---

## Workspaces

### `GET /api/workspaces`

List every workspace (cross-workspace; the only un-scoped listing).

**Response** (`WorkspaceListResponse`): `{ "workspaces": WorkspaceSummary[] }`.

`WorkspaceSummary`:

| Field              | Type             | Notes                                                    |
| ------------------ | ---------------- | -------------------------------------------------------- |
| `id`               | string           | Workspace id (`ws_...`).                                 |
| `slug`             | string           |                                                          |
| `name`             | string           |                                                          |
| `description`      | string \| null   |                                                          |
| `owner_id`         | string \| null   |                                                          |
| `created_at`       | datetime         |                                                          |
| `experiment_count` | int              |                                                          |
| `last_run_at`      | datetime \| null | Max `updated_at` over the workspace's iteration records. |

### `GET /api/workspaces/{workspace_id}`

A single workspace with a recent-health rollup.

**Response** (`WorkspaceResponse`):

| Field                                                         | Type          | Notes                                                                                                                      |
| ------------------------------------------------------------- | ------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `id`, `slug`, `name`, `description`, `owner_id`, `created_at` | —             | As above.                                                                                                                  |
| `experiment_count`                                            | int           |                                                                                                                            |
| `recent_health`                                               | float \| null | Fraction of the 20 most recent iterations whose decision outcome is `keep_candidate`; `null` when there are no iterations. |

**Errors:** `404` when the workspace does not exist.

### `POST /api/workspaces`

Create (seed) a new workspace. The `X-SelfEvals-User` header becomes the
workspace `owner_id` (default `"local"`).

**Request body** (`CreateWorkspaceRequest`):

| Field         | Type           | Notes                 |
| ------------- | -------------- | --------------------- |
| `slug`        | string         | Required, 1–63 chars. |
| `name`        | string \| null | Defaults to `slug`.   |
| `description` | string \| null |                       |

**Response** (`WorkspaceResponse`, status `201`). `experiment_count` is `0`
and `recent_health` is `null` for a freshly seeded workspace.

---

## Experiments

### `GET /api/workspaces/{workspace_id}/experiments`

Paginated experiment list.

**Query params:**

| Param     | Type   | Notes                                                                                                                                           |
| --------- | ------ | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `limit`   | int    | 1–500, default 100.                                                                                                                             |
| `offset`  | int    | ≥ 0, default 0.                                                                                                                                 |
| `state`   | string | Optional. Filter by experiment state (`draft`, `queued`, `running`, `paused`, `completed`, `aborted`, `superseded`); an invalid value is `422`. |
| `feature` | string | Optional. Keep only experiments whose `taxonomy.target_features` contains this value.                                                           |

Filters apply before pagination, so `total`/`has_more` describe the filtered
set.

**Response** (`ExperimentListPage`):

| Field      | Type                  | Notes                               |
| ---------- | --------------------- | ----------------------------------- |
| `items`    | `ExperimentSummary[]` | The page.                           |
| `total`    | int                   | Total experiments in the workspace. |
| `limit`    | int                   | Echoed.                             |
| `offset`   | int                   | Echoed.                             |
| `has_more` | bool                  | `(offset + limit) < total`.         |

`ExperimentSummary`:

| Field                      | Type     | Notes                                   |
| -------------------------- | -------- | --------------------------------------- |
| `id`                       | string   | `exp_...`.                              |
| `name`, `goal`             | string   |                                         |
| `mode`                     | string   | Experiment mode.                        |
| `state`                    | string   | Experiment lifecycle state.             |
| `primary_metric`           | string   | Name of the primary target metric.      |
| `primary_target`           | object   | `{ "operator": str, "value": number }`. |
| `proposer_strategy`        | string   | `manual` \| `grid` \| `random`.         |
| `max_iterations`           | int      |                                         |
| `created_at`, `updated_at` | datetime |                                         |
| `iteration_count`          | int      |                                         |

### `POST /api/workspaces/{workspace_id}/experiments/run`

Launch an experiment. **Non-blocking:** validates and persists synchronously,
then runs the optimization loop on a background thread and returns `202`
immediately. Follow progress by polling
`GET /api/workspaces/{workspace_id}/experiments/{experiment_id}` — its
`summary.state` climbs `draft → queued → running → completed` (or `aborted` on
failure). Persistence is on: the experiment, its iterations, and traces are
written to storage, exactly like `selfevals run`.

**Request** (`RunExperimentRequest`) — provide exactly one of:

| Field            | Type           | Notes                                                                                                                                                             |
| ---------------- | -------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `spec_path`      | string \| null | Path/name of a YAML spec on the server's disk.                                                                                                                    |
| `spec_inline`    | object \| null | The spec as a JSON object (same shape as the YAML). Must embed cases under `dataset.cases_inline`; a `dataset.cases_path` has no base to resolve and is rejected. |
| `max_iterations` | int \| null    | Optional override (≥ 1).                                                                                                                                          |
| `reps`           | int \| null    | Optional repetitions per case (≥ 1).                                                                                                                              |
| `persist_traces` | string \| null | Optional: `none` \| `all` \| `failed`.                                                                                                                            |

The path `workspace_id` is authoritative — it overrides any `workspace:` in the
spec, so the experiment always lands in the workspace from the URL.

**Response** (`RunExperimentResponse`, `202`):

| Field           | Type           | Notes                                                                      |
| --------------- | -------------- | -------------------------------------------------------------------------- |
| `experiment_id` | string         | `exp_...`.                                                                 |
| `workspace_id`  | string         | The path workspace.                                                        |
| `state`         | string         | Starting state at acknowledgement.                                         |
| `run_id`        | string \| null | `null` here; trace run ids surface on the iterations as they are produced. |

**Errors:** `422` when the spec does not validate, yields zero cases, or the
source combination is wrong (neither/both of `spec_path`/`spec_inline`); `409`
when the target experiment already has an active run (`queued`/`running`/
`paused`).

### `GET /api/workspaces/{workspace_id}/experiments/{experiment_id}`

Full experiment detail, including a reconstructed JSON report.

**Response** (`ExperimentDetailResponse`):

| Field            | Type                 | Notes                                                                                                                                                                                                                           |
| ---------------- | -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `summary`        | `ExperimentSummary`  |                                                                                                                                                                                                                                 |
| `result`         | object \| null       | The reporter's JSON shape (`render_json`, see [`json_report_schema.md`](json_report_schema.md)); `null` when the experiment has not run. Reconstructed from storage, including each iteration's `funnel` and `failure_reasons`. |
| `iterations`     | `IterationSummary[]` |                                                                                                                                                                                                                                 |
| `best_iteration` | object \| null       | The winning iteration (highest primary metric), lifted from `result.best_iteration` to a first-class field. Same per-iteration shape as the reporter; `null` when the experiment has no iterations.                             |

`IterationSummary`:

| Field                  | Type           | Notes                                                                 |
| ---------------------- | -------------- | --------------------------------------------------------------------- |
| `id`                   | string         | `itr_...`.                                                            |
| `iteration`            | int            | Zero-based index.                                                     |
| `state`                | string         |                                                                       |
| `hypothesis`           | string         |                                                                       |
| `proposed_parameters`  | object         |                                                                       |
| `primary_metric_name`  | string \| null |                                                                       |
| `primary_metric_value` | float \| null  |                                                                       |
| `delta_vs_best`        | float \| null  | Primary value minus the best-so-far value seen in earlier iterations. |
| `decision_outcome`     | string \| null |                                                                       |
| `decision_rationale`   | string \| null | Automated rationale.                                                  |
| `cost_usd`             | float \| null  |                                                                       |
| `duration_seconds`     | float \| null  |                                                                       |
| `trace_run_ids`        | string[]       | Run ids of the traces this iteration produced.                        |
| `created_at`           | datetime       |                                                                       |

**Errors:** `404` when the experiment does not exist.

### `GET /api/workspaces/{workspace_id}/experiments/{experiment_id}/iterations`

**Response** (`IterationListResponse`): `{ "iterations": IterationSummary[] }`.

### `GET /api/workspaces/{workspace_id}/experiments/{experiment_id}/decisions`

The decision audit trail, one entry per iteration that has a decision,
sorted by iteration.

**Response:** `list[DecisionRecordResponse]`, each:

| Field                 | Type           | Notes                                     |
| --------------------- | -------------- | ----------------------------------------- |
| `id`                  | string         | Decision id (`dec_...`).                  |
| `iteration`           | int            |                                           |
| `outcome`             | string         |                                           |
| `automated_rationale` | string         |                                           |
| `human_rationale`     | string \| null | The human reviewer's notes, when present. |
| `metrics_snapshot`    | object         | The metrics at decision time.             |
| `created_at`          | string         | ISO-8601.                                 |

### `GET /api/workspaces/{workspace_id}/experiments/{experiment_id}/compare`

Server-rendered structured diff of two iterations. All comparison math is
done by the reporter's `compute_compare` — the single source of truth shared
with the CLI `compare` command.

**Query params:** `a` (iteration A record id, required), `b` (iteration B
record id, required).

**Response** (`CompareResponse`):

| Field                          | Type                 | Notes                                                                                                                                                                           |
| ------------------------------ | -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `a_id`, `b_id`                 | string               | Iteration record ids.                                                                                                                                                           |
| `a_iteration`, `b_iteration`   | int                  | Iteration indices.                                                                                                                                                              |
| `a_created_at`, `b_created_at` | string               |                                                                                                                                                                                 |
| `a_decision`, `b_decision`     | string \| null       | Decision outcomes.                                                                                                                                                              |
| `proposal_diff`                | `CompareParamRow[]`  | Per-parameter: `{ key, a, b, changed }` (a/b are stringified values).                                                                                                           |
| `metrics_diff`                 | `CompareMetricRow[]` | Per-metric: `{ name, a, b, delta }` (a/b/delta are floats or null).                                                                                                             |
| `failure_modes`                | object               | `{ only_a: {mode: count}, only_b: {mode: count}, common: {mode: [count_a, count_b]} }`.                                                                                         |
| `funnel_diff`                  | `CompareFunnelRow[]` | Per funnel path: `{ path, a, b, delta }`.                                                                                                                                       |
| `recommendation`               | object               | `{ kind, winner?, metric_name?, a_metric_name?, b_metric_name?, a_value?, b_value?, delta?, new_failure_modes[] }`. `kind` ∈ `winner` \| `tie` \| `different_metric` \| `none`. |
| `holdout_status`               | string               | Always `"unavailable"` — an `IterationRecord` carries no split classification, so no holdout number is fabricated.                                                              |

**Errors:** `404` when one or both iterations are not found; `400` when the
two iterations belong to a different experiment than the one in the path (not
an apples-to-apples comparison).

---

## Iterations

### `GET /api/workspaces/{workspace_id}/iterations/{iteration_id}`

A single iteration record plus its decision.

**Response:** `{ "iteration": object, "decision": object | null }` — both are
the raw JSON dumps of `IterationRecord` and `DecisionRecord`.

**Errors:** `404` when the iteration does not exist.

### `GET /api/workspaces/{workspace_id}/iterations/{iteration_id}/funnel`

The grader funnel drill-down for one iteration, read directly from the
persisted `IterationRecord.metrics.funnel` (the source of truth).

**Response** (`FunnelResponse`):

| Field          | Type   | Notes                                                                                                                                                     |
| -------------- | ------ | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `iteration_id` | string |                                                                                                                                                           |
| `iteration`    | int    |                                                                                                                                                           |
| `nodes`        | object | Map of top-level `key` → `FunnelNodeResponse`. Empty `{}` when no grader emitted a breakdown (the common case for the pingpong example — _not_ an error). |

`FunnelNodeResponse` (recursive):

| Field                 | Type          | Notes                                        |
| --------------------- | ------------- | -------------------------------------------- |
| `key`                 | string        |                                              |
| `count`               | int           | How many breakdown nodes rolled up here.     |
| `mean_score`          | float \| null | Weight-weighted mean of contributing scores. |
| `total_weight`        | float         |                                              |
| `label_counts`        | object        | `{ label: count }`.                          |
| `failure_mode_counts` | object        | `{ failure_mode: count }`.                   |
| `children`            | object        | Map of `key` → nested `FunnelNodeResponse`.  |

**Errors:** `404` when the iteration does not exist.

---

## Traces & threads

### `GET /api/workspaces/{workspace_id}/traces/{trace_id}`

A single trace. `trace_id` accepts either the entity id (`tr_...`) or the
`run_id` (`run_...`) as a fallback.

**Response** (`TraceResponse`):

| Field             | Type             | Notes                                                          |
| ----------------- | ---------------- | -------------------------------------------------------------- |
| `id`              | string           | Trace entity id.                                               |
| `run_id`          | string           |                                                                |
| `experiment_id`   | string \| null   |                                                                |
| `experiment_name` | string \| null   | Resolved for nicer titling; null for orphan/standalone traces. |
| `iteration`       | int \| null      |                                                                |
| `thread_id`       | string \| null   |                                                                |
| `thread_position` | int \| null      |                                                                |
| `final_state`     | string           |                                                                |
| `started_at`      | datetime         |                                                                |
| `ended_at`        | datetime \| null |                                                                |
| `spans`           | `SpanSummary[]`  |                                                                |
| `metrics`         | object           | The trace's metrics, JSON-dumped.                              |

`SpanSummary`:

| Field         | Type           | Notes                                                                                                                                                                        |
| ------------- | -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `id`          | string         |                                                                                                                                                                              |
| `parent_id`   | string \| null |                                                                                                                                                                              |
| `kind`        | string         | Span kind (LLMCall, ToolCall, Retrieval, Decision, …).                                                                                                                       |
| `name`        | string         |                                                                                                                                                                              |
| `started_at`  | datetime       |                                                                                                                                                                              |
| `duration_ms` | int            |                                                                                                                                                                              |
| `detail`      | object         | Kind-specific high-value fields (model, tokens, cost, TTFT, tool name/args/result pointers, retrieval query/top_k, etc.) for the inspector to render without a second fetch. |

**Errors:** `404` when the trace does not exist.

### `GET /api/workspaces/{workspace_id}/threads/{thread_id}`

Every trace sharing a `thread_id`, assembled into an ordered multi-turn
conversation. Turns are ordered by `thread_position` when set, falling back to
`started_at`.

**Response** (`ThreadResponse`):

| Field        | Type           | Notes |
| ------------ | -------------- | ----- |
| `thread_id`  | string         |       |
| `turn_count` | int            |       |
| `turns`      | `ThreadTurn[]` |       |

`ThreadTurn`:

| Field            | Type             | Notes                                                 |
| ---------------- | ---------------- | ----------------------------------------------------- |
| `trace_id`       | string           |                                                       |
| `run_id`         | string           |                                                       |
| `position`       | int              | `thread_position` when set, else the 0-based ordinal. |
| `experiment_id`  | string \| null   |                                                       |
| `iteration`      | int \| null      |                                                       |
| `final_state`    | string           |                                                       |
| `started_at`     | datetime         |                                                       |
| `ended_at`       | datetime \| null |                                                       |
| `primary_grade`  | string \| null   | Label of the first grader result.                     |
| `grader_results` | object[]         | Per-grader results for this turn.                     |
| `metrics`        | object           | The turn's trace metrics.                             |

**Errors:** `404` when no trace carries the thread id.

### `GET /api/runs/active`

Currently-streaming runs (from the in-memory span broker).

**Response** (`ActiveRunsResponse`): `{ "runs": [{ "workspace_id": str, "run_id": str }] }`.

### `GET /api/workspaces/{workspace_id}/traces/{run_id}/stream`

Server-Sent Events for a live run. Emits `snapshot` (the full trace), `span`
(one span at a time as it arrives), `ping` (a heartbeat every ~15s), and
`complete` (the final state). Returns a `text/event-stream`.

---

## Object payloads

### `GET /api/workspaces/{workspace_id}/payloads`

Resolve an object-store pointer to its raw bytes. Used to lazy-load LLM
prompts, tool-call args/results, and retrieval payloads in the trace viewer
(spans carry only the pointers + hashes).

**Query param:** `pointer` — of the form
`oss://<workspace_id>/sha256:<hex>` (required).

**Response:** the stored bytes. JSON-looking payloads are served as
`application/json`, other text as `text/plain; charset=utf-8`, and
non-UTF-8 bytes as `application/octet-stream`.

**Errors:** `400` when the pointer is malformed or its workspace does not
match the path workspace (a leaked pointer can't be read via another
workspace's URL); `404` when the pointer is not in the object store; `500`
when the stored content's hash no longer matches the pointer (corruption).

---

## Anchor set

### `GET /api/workspaces/{workspace_id}/anchor-set`

A longitudinal view: the per-experiment latest completed iterations, sorted
by creation time, so a trend chart has shape. (True anchor-set semantics —
repeated reruns of a canonical case set — land later.)

**Response:** `list[AnchorPoint]`:

| Field                  | Type   | Notes                                           |
| ---------------------- | ------ | ----------------------------------------------- |
| `experiment_id`        | string |                                                 |
| `experiment_name`      | string |                                                 |
| `iteration`            | int    |                                                 |
| `primary_metric_name`  | string |                                                 |
| `primary_metric_value` | float  |                                                 |
| `decision_outcome`     | string | `"unknown"` when the iteration has no decision. |
| `created_at`           | string | ISO-8601.                                       |
