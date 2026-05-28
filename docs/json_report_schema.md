# JSON Report Schema

`selfevals report <ws> <exp> --format json` (and `selfevals run … --format
json`) emit a stable JSON document describing an `OptimizationResult`. The
shape is produced by `src/selfevals/reporter/json_report.py`
(`render_json` / `to_dict`). Downstream tooling (CI bots, dashboards,
`brain_os`) reads it, so the structure is versioned via `schema_version`.

The same JSON shape is also returned inside the HTTP API's
`ExperimentDetailResponse.result` field — see
[`api_reference.md`](api_reference.md). For authoring experiments, see
[`eval_config.md`](eval_config.md).

```bash
selfevals --db ./selfevals.sqlite report <workspace_id> <experiment_id> --format json
```

## Root keys

| Key | Type | Notes |
|-----|------|-------|
| `schema_version` | string | Currently `"1"`. Bump before changing any key. |
| `experiment` | object | The experiment contract — see below. |
| `termination` | object | `{ reason, iterations_run }`. `reason` is the loop's stop reason (e.g. `loaded_from_storage` when reconstructed via `report`, or a convergence/exhaustion reason on a live run). |
| `cost_time` | object | Aggregate cost & time — see below. |
| `best_iteration` | object \| null | The winning iteration (same per-iteration shape as `iterations[]`); `null` when there are no iterations. |
| `iterations` | array | Every iteration, in order. |

### `experiment`

| Key | Type |
|-----|------|
| `id` | string |
| `name` | string |
| `goal` | string |
| `mode` | string |
| `state` | string |
| `primary_metric` | string |
| `primary_target` | object `{ operator, value }` |
| `guardrails` | array of `{ name, operator, value }` |
| `proposer_strategy` | string |
| `max_iterations` | int |

### `cost_time`

| Key | Type | Notes |
|-----|------|-------|
| `cost_total_usd` | float \| null | `null` when no cost was recorded. |
| `cost_per_iteration_usd` | float \| null | |
| `cost_per_case_usd` | float \| null | |
| `time_total_seconds` | float | |
| `time_per_iteration_seconds` | float | |
| `time_per_case_seconds` | float | |
| `iterations` | int | |
| `cases_run` | int | |

## Per-iteration keys

Each entry in `iterations[]` (and `best_iteration`):

| Key | Type | Notes |
|-----|------|-------|
| `iteration` | int | Zero-based index. |
| `hypothesis` | string | The proposal's hypothesis. |
| `parameters` | object | The proposed parameter configuration. |
| `metrics` | object | `{ primary: { name, value }, guardrails: {name: value}, reliability: {name: value} }`. |
| `failure_modes` | object | `{ failure_mode: count }` across the iteration. |
| `cache` | object | `{ hits, llm_calls }` — count of cache-hit LLM spans and total LLM-call spans across the iteration's traces. |
| `funnel` | object | Map of top-level `key` → recursive funnel node (`{ key, count, mean_score, total_weight, label_counts, failure_mode_counts, children }`). |
| `totals` | object | `{ cost_usd, duration_ms, case_count }`. |
| `decision` | object | `{ outcome, rationale }`. |
| `records` | object | `{ iteration_id, decision_id }`. |
| `failure_reasons` | array | Deduplicated grader rationales for non-passing grades — see below. |

### `cache`

`{ "hits": N, "llm_calls": M }`. `hits` counts spans whose `cache_hit` is
true; `llm_calls` is the total number of `LLMCallSpan`s. Lets a consumer read
cache effectiveness without reading raw traces.

### `failure_reasons`

A compact, deduplicated list of *why* grading failed. Each iteration runs
many cases × repetitions; rather than emit every grade, the reporter walks
each persisted trace, keeps grader results whose label is not a pass (and
have a non-empty reason), and deduplicates on `(grader, label, reason)`. Each
entry:

| Key | Type |
|-----|------|
| `grader` | string |
| `label` | string (e.g. `"fail"`) |
| `score` | float \| null |
| `reason` | string |
| `failure_modes` | array of string |

> **Important caveat — live vs. reconstructed.** `funnel` and
> `failure_reasons` are derived from per-case `case_runs` and grader
> breakdowns that exist **only in memory during a live `run`**. When an
> experiment is *reconstructed from storage* — i.e. `selfevals report` and
> the HTTP API's `ExperimentDetailResponse.result` — those live-only fields
> are not rehydrated, so `funnel` is `{}` and `failure_reasons` is `[]`.
> The funnel for a stored iteration is still available via the dedicated
> endpoint `GET /api/workspaces/{ws}/iterations/{id}/funnel`, which reads the
> persisted `IterationRecord.metrics.funnel` directly
> (see [`api_reference.md`](api_reference.md)). `cache`, `failure_modes`,
> `metrics`, and `totals` are persisted and present in both cases.

## Example (live run)

A real `run --format json` against the bundled pingpong example
(`--max-iterations 2`), trimmed to one iteration to show the populated
`funnel`, `cache`, and `failure_reasons`:

```json
{
  "schema_version": "1",
  "experiment": {
    "id": "exp_…",
    "name": "pingpong baseline",
    "goal": "warm up the end-to-end loop with a trivial echo agent",
    "mode": "handoff",
    "state": "draft",
    "primary_metric": "pass@1",
    "primary_target": { "operator": ">=", "value": 0.5 },
    "guardrails": [],
    "proposer_strategy": "grid",
    "max_iterations": 2
  },
  "termination": { "reason": "…", "iterations_run": 2 },
  "cost_time": {
    "cost_total_usd": null,
    "cost_per_iteration_usd": null,
    "cost_per_case_usd": null,
    "time_total_seconds": 0.006,
    "time_per_iteration_seconds": 0.003,
    "time_per_case_seconds": 0.0015,
    "iterations": 2,
    "cases_run": 4
  },
  "iterations": [
    {
      "iteration": 0,
      "hypothesis": "grid[0]: {'level': 0.0}",
      "parameters": { "model_params": { "level": 0.0 } },
      "metrics": {
        "primary": { "name": "pass@1", "value": 0.0 },
        "guardrails": {
          "latency_ms_p50": 3.0,
          "latency_ms_p95": 5.7,
          "latency_ms_p99": 5.94,
          "latency_ms_per_case_avg": 3.0
        },
        "reliability": { "pass@1": 0.0 }
      },
      "failure_modes": { "missing_required_substring": 2 },
      "cache": { "hits": 0, "llm_calls": 2 },
      "funnel": {
        "conversation": {
          "key": "conversation",
          "count": 2,
          "mean_score": 0.0,
          "total_weight": 2.0,
          "label_counts": { "fail": 2 },
          "failure_mode_counts": {},
          "children": {
            "turn_0": {
              "key": "turn_0",
              "count": 2,
              "mean_score": null,
              "total_weight": 0.0,
              "label_counts": { "fail": 2 },
              "failure_mode_counts": {},
              "children": {}
            }
          }
        }
      },
      "totals": { "cost_usd": 0.0, "duration_ms": 6, "case_count": 2 },
      "decision": {
        "outcome": "investigate",
        "rationale": "first iteration below target: pass@1=0 vs target >= 0.5; investigate before bailing"
      },
      "records": { "iteration_id": "itr_…", "decision_id": "dec_…" },
      "failure_reasons": [
        {
          "grader": "deterministic",
          "label": "fail",
          "score": 0.0,
          "reason": "missing_required_substring:pong",
          "failure_modes": ["missing_required_substring"]
        }
      ]
    }
  ],
  "best_iteration": { "…": "iteration 1, pass@1=1.0, decision keep_candidate" }
}
```

> Note: keys are emitted sorted (the reporter uses `sort_keys=True`); the
> ordering above is rearranged for readability. The pingpong agent makes no
> real LLM calls, so `cost_total_usd` is `null` and `cache.hits` is `0`.
