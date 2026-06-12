# HTTP API Reference

The canonical reference for the selfevals HTTP bridge — a read-mostly
FastAPI service over configured storage (`src/selfevals/api/app.py`). Every
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
- **Storage:** resolved from an explicit `--db` CLI flag / `db_path` build arg
  first, then `SELFEVALS_STORAGE_URL`, then `SELFEVALS_DB`, then
  `./selfevals.sqlite`. Use a plain path or `sqlite:///...` for SQLite; use
  `postgresql://...` with the `selfevals[postgres]` extra for Postgres.
- **Live broker:** in-memory by default. Set `SELFEVALS_REDIS_URL` with the
  `selfevals[redis]` extra to fan out live SSE events through Redis Streams
  across API processes.
- **Errors:** `404` for an unknown entity, `400` for a malformed request
  (e.g. a cross-experiment compare or an invalid object-store pointer),
  `500` for a stored-payload hash mismatch (corruption).

## Running the API

```bash
python -m selfevals.api --host 127.0.0.1 --port 8000 --db ./selfevals.sqlite
```

`--reload` enables autoreload. `--db` is an explicit override; when it is
absent, `SELFEVALS_STORAGE_URL` wins over `SELFEVALS_DB`.

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

## Metrics

Metrics endpoints summarize persisted traces for production agent monitoring:
pass/fail rates, failure modes, tool usage, cost, tokens, and latency. With
Postgres storage they read normalized fact tables (`trace_grader_results`,
`tool_calls`, `llm_calls`, and `traces`). SQLite remains supported for local
quickstarts by scanning canonical Trace JSON.

All metrics endpoints accept:

| Param           | Type     | Notes                                                                 |
| --------------- | -------- | --------------------------------------------------------------------- |
| `from`          | datetime | Optional inclusive lower bound on `trace.environment.started_at`.      |
| `to`            | datetime | Optional inclusive upper bound on `trace.environment.started_at`.      |
| `experiment_id` | string   | Optional. Restrict metrics to one experiment.                         |

### `GET /api/workspaces/{workspace_id}/metrics/pass-rate`

Counts grader labels and returns per-label rates.

Extra query params:

| Param    | Type   | Notes                              |
| -------- | ------ | ---------------------------------- |
| `grader` | string | Optional. Restrict to one grader. |

**Response** (`PassRateMetricsResponse`): `{ workspace_id, window, experiment_id,
total, items }`, where each item is `{ grader, label, count, rate }`.

### `GET /api/workspaces/{workspace_id}/metrics/failure-modes`

Counts failure-mode tags emitted by grader results.

Extra query params:

| Param    | Type   | Notes                              |
| -------- | ------ | ---------------------------------- |
| `grader` | string | Optional. Restrict to one grader. |

**Response** (`FailureModeMetricsResponse`): each item is
`{ failure_mode, count, rate }`.

### `GET /api/workspaces/{workspace_id}/metrics/tools`

Aggregates tool calls by tool name and status.

Extra query params:

| Param       | Type   | Notes                            |
| ----------- | ------ | -------------------------------- |
| `tool_name` | string | Optional. Restrict to one tool. |

**Response** (`ToolMetricsResponse`): each item is
`{ tool_name, status, count, error_count, avg_duration_ms, retry_count }`.

### `GET /api/workspaces/{workspace_id}/metrics/cost`

Aggregates LLM call cost by provider and model.

Extra query params:

| Param   | Type   | Notes                             |
| ------- | ------ | --------------------------------- |
| `model` | string | Optional. Restrict to one model. |

**Response** (`CostMetricsResponse`): each item is
`{ provider, model, call_count, total_cost_usd, avg_cost_usd }`.

### `GET /api/workspaces/{workspace_id}/metrics/tokens`

Aggregates LLM token usage by provider and model.

Extra query params:

| Param   | Type   | Notes                             |
| ------- | ------ | --------------------------------- |
| `model` | string | Optional. Restrict to one model. |

**Response** (`TokenMetricsResponse`): each item is
`{ provider, model, call_count, input_tokens, output_tokens, reasoning_tokens,
total_tokens }`.

### `GET /api/workspaces/{workspace_id}/metrics/latency`

Returns percentile latency rows for trace duration, tool duration, and model
time-to-first-token.

**Response** (`LatencyMetricsResponse`): each item is
`{ metric, count, p50_ms, p95_ms, p99_ms }`, where `metric` is one of
`trace_duration_ms`, `tool_duration_ms`, or `ttft_ms`.

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
creates a durable `RunJob`, then returns `202` immediately. If
`SELFEVALS_REDIS_URL` is configured, the job is delivered through Redis Streams
and consumed by `selfevals worker runs`; otherwise local/dev mode falls back to
the historical in-process background thread. Follow progress by polling
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

**Response** (`RunExperimentResponse`): `{ "experiment_id": str, "workspace_id": str, "state": str, "run_id": null, "job_id": str }`.

### `POST /api/workspaces/{workspace_id}/experiments/{experiment_id}/cancel`

Request cancellation for the latest durable run job on an experiment. Queued
jobs are cancelled immediately and the experiment is marked `aborted`. Running
jobs observe cancellation at worker boundaries and are not force-killed.

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

### `GET /api/workspaces/{workspace_id}/experiments/{experiment_id}/cases`

The eval cases the experiment ran, persisted at launch and stamped with the
`experiment_id`. Holdout cases are included and flagged (`holdout: true`) — the
set is reported in full, not trimmed to the optimization cases. Ordered by
`name`. Returns an empty list (not `404`) for an experiment with no persisted
cases (e.g. one that predates case persistence).

**Response** (`CaseListResponse`):

| Field           | Type            | Notes                            |
| --------------- | --------------- | -------------------------------- |
| `cases`         | `CaseSummary[]` | The cases, ordered by name.      |
| `total`         | int             | `len(cases)`.                    |
| `holdout_count` | int             | How many of `cases` are holdout. |

Each `CaseSummary`:

| Field             | Type           | Notes                                                           |
| ----------------- | -------------- | --------------------------------------------------------------- |
| `id`              | string         | Case id (`ec_...`) — navigable.                                 |
| `name`            | string         |                                                                 |
| `task_type`       | string         |                                                                 |
| `modalities`      | string[]       |                                                                 |
| `input`           | object         | Raw payload fed to the agent. The FE renders it directly.       |
| `graders`         | string[]       | Grader names applied to this case (empty = experiment default). |
| `holdout`         | bool           | Reserved from proposers when true.                              |
| `is_conversation` | bool           | True when `input` carries a `messages` key (multi-turn).        |
| `feature`         | string \| null | Taxonomy target feature.                                        |
| `level`           | string \| null | Taxonomy level.                                                 |
| `dataset_type`    | string \| null | Taxonomy dataset type.                                          |

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

Currently-streaming runs (from the configured span broker: in-memory locally,
Redis Streams when `SELFEVALS_REDIS_URL` is set).

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
