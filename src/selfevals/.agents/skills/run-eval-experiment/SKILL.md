---
name: run-eval-experiment
description: Author, run, and read a selfevals experiment end-to-end — write the YAML spec (cases, expected, graders, proposer, target, run block), estimate its cost, run it with the CLI, and interpret the markdown/JSON report (metrics, funnel, cache, failure_reasons) and the web UI. Use when a human or agent wants to write/run/interpret a selfevals experiment or navigate its dashboard. For choosing graders and designing cases, use `design-your-dataset`; for wiring the agent, use `connect-your-agent`; for classifying failures after a run, hand off to `error-analysis`; for baselines/regression/scale, use `iterate-and-ship`. Full key reference: `docs/eval_config.md`; report schema: `docs/json_report_schema.md`; adapters: `docs/adapters.md`.
---

# Run an eval experiment (author → estimate → run → read)

You are driving selfevals end-to-end: turn an agent + a dataset into a structured
experiment, run it, and read the result. selfevals owns the loop (proposer →
agent → graders → decision → persistence); **your agent** is the thing under
test, and selfevals calls _it_ — **selfevals never calls a provider itself**.

This is the practitioner's path. Reference docs: `docs/eval_config.md` (every
YAML key), `docs/json_report_schema.md` (every report key),
`docs/api_reference.md` (web/API surface), `docs/adapters.md` (wiring agents).

## 0. Preflight

- Confirm the CLI: `selfevals --help`. If the project uses `uv`, prefix every
  command with `uv run`. Match what the project already uses.
- **Storage is Postgres** (the framework is Postgres-only). For a first smoke run
  you don't need it — use `--no-persist`. To persist, set
  `SELFEVALS_STORAGE_URL` (e.g. `postgresql://localhost:5433/selfevals`), or pass
  the **global** `--db <postgres-url>` flag *before* the subcommand:
  `selfevals --db postgresql://… run …`. SQLite is **legacy** — only the one-shot
  `selfevals migrate-sqlite ./old.sqlite --to "$SELFEVALS_STORAGE_URL"` import,
  never a live backend. `docker compose up -d postgres redis` brings up local
  Postgres + Redis (see `.env.example`).
- Need a workspace? `selfevals init <slug>` creates (or re-opens) one and prints
  its id. The example spec carries a `workspace:` key, so for a first smoke run
  you can skip this and use `--no-persist`.
- Want a runnable starting point? `selfevals examples copy pingpong` writes an
  `evals/` tree. For a spec wiring up **every** grader type and funnel match kind
  (offline), copy `showcase` instead: `selfevals examples copy showcase`.

## 1. Write the spec (YAML)

A spec has four top-level blocks: `workspace`, `experiment`, `dataset`, `agent`
(plus an optional top-level `graders:` block). Minimal working shape (mirrors
`evals/experiments/example_pingpong.yaml`):

```yaml
workspace: ws_01HZZZZZZZZZZZZZZZZZZZZZZZ

experiment:
  name: pingpong baseline
  goal: warm up the end-to-end loop with a trivial echo agent
  mode: handoff
  taxonomy:
    target_features: [commerce.product_resolution]
    dataset_types: [capability]
  target:
    primary: { name: pass@1, operator: ">=", value: 0.5 }
    guardrails:                       # optional
      - { name: cost_usd, operator: "<=", value: 0.05 }
  proposer:
    strategy: grid                    # manual | grid | random | llm_proposer
  search_space:
    model_params:
      temperature: [0.0, 0.5, 1.0]
  run:
    sandbox: mock                     # mock for offline; live_sandboxed for real calls
    max_iterations: 4
    repetitions_per_case: 1
    parallelism: 8                    # in-flight ceiling (ge=1 le=64); fans out cases
    persist_traces: failed            # none | failed | all
    error_analysis:                   # optional: auto-stage analysis on failure
      enabled: true
      trigger: { when: fail_rate_above, threshold: 0.10 }
      scope: failed_only

dataset:
  cases_path: ../datasets/pingpong.jsonl   # or `ref: ds_xxx` for a persisted one

agent:
  entrypoint: selfevals.examples.pingpong:run   # embedded; see connect-your-agent
```

The `agent:` block is transport-tagged — embedded (`entrypoint`), `type: cli`, or
`type: http`. For the contract and shims, use the **`connect-your-agent`** skill.
For cases, `expected`, and choosing graders, use **`design-your-dataset`**.

### Graders in the spec

Cases reference graders by name. A name resolves to a **registry grader**
(`deterministic`, `guardrail`, `artifact_completeness`, `trajectory`,
`set_match`) or to a grader you declare in the top-level `graders:` block
(`set_match` tuned, `llm_judge`, `judge_panel`, `funnel`, `confusion`). Example:

```yaml
graders:
  - type: judge_panel
    name: quality_panel
    rubric: "Did the agent resolve every requested product?"
    n_judges: 3
    consensus: majority
    judge_entrypoint: selfevals.examples.showcase:judge   # offline, deterministic
  - type: confusion
    name: category_class
    params: { extract: category }
```

Referencing an unregistered name raises "Grader 'x' not registered" listing the
available names. The kitchen-sink reference is
`evals/experiments/example_showcase.yaml`.

### Proposers

`experiment.proposer.strategy` selects how iterations are proposed. Implemented:
`manual`, `grid`, `random`, `llm_proposer`. (`bayesian` / `bandit` /
`evolutionary` are reserved and raise "not implemented".) `grid` and `random`
walk `experiment.search_space`; `llm_proposer` lets a model propose the next
parameters; the grid proposer exhausts combinations and raises
`SearchSpaceExhaustedError` when done.

## 2. Estimate the cost (before spending tokens)

```bash
selfevals estimate --cases 50 --space-size 8 --reps 3 --cost-per-call 0.01
```

A pure dry-run — touches neither DB nor agent. Use it to sanity-check a run's
budget before launching a live one.

## 3. Run

```bash
# Smoke run, no persistence, markdown report:
selfevals run evals/experiments/example_pingpong.yaml --no-persist

# Persisted run (Postgres), capped iterations, JSON report, keep failed traces:
selfevals --db "$SELFEVALS_STORAGE_URL" run evals/experiments/example_pingpong.yaml \
    --max-iterations 4 --reps 3 --format json --persist-traces failed
```

`run` flags (verified via `--help`): `spec` (positional), `--workspace`,
`--max-iterations`, `--reps`, `--format {markdown,json}`, `--no-persist`,
`--persist-traces {none,all,failed}`. Persisted failed traces are what
`analyze pull` later feeds to error analysis, so set `--persist-traces failed`
(or `run.persist_traces: failed` in the spec) when you plan to analyze.

Re-render a report from already-persisted iterations:

```bash
selfevals report <workspace_id> <experiment_id>            # markdown
selfevals report <workspace_id> <experiment_id> --format json
```

Diff two **iterations of the same experiment** by primary metric (args are
**iteration ids** + workspace, not experiment ids):

```bash
selfevals compare <workspace_id> <iter_a_id> <iter_b_id>
```

Other useful commands: `selfevals experiment list <ws>`,
`selfevals iteration list <ws> <exp>`, `selfevals dataset list <ws>`,
`selfevals skills list`. For durable background execution at scale
(`worker runs` / `worker sweeper`, Redis), see the **`iterate-and-ship`** skill.

## 4. Read the report

**Markdown** (default) is the human skim. **JSON** (`--format json`) is the
machine shape consumed by CI bots / dashboards (versioned; see
`docs/json_report_schema.md`). Per-iteration keys worth knowing:

- `metrics` — `primary` ({name, value}), `guardrails`, `reliability`.
- `failure_modes` — counts per failure-mode id.
- `funnel` — per-grader breakdown of how cases flowed (drill-down for the Funnel
  tab).
- `cache` — `{hits, llm_calls}`: cache hits vs actual LLM calls this iteration.
- `failure_reasons` — a **deduplicated** list of non-passing grader rationales:
  `[{grader, label, score, reason, failure_modes}]`, one entry per distinct
  `(grader, label, reason)`.

`cache`, `funnel`, and `failure_reasons` are **additive / informational** — they
describe the run, they never change the decision.

## 5. Use the web UI

`selfevals serve` starts the FastAPI bridge and (when a web build is present) the
SvelteKit UI in one process:

```bash
selfevals serve                                   # auto-detects web build
selfevals serve --web-dist web/build --port 8080  # explicit build dir
selfevals serve --no-web                          # API only (headless)
```

Defaults: host `127.0.0.1`, port `8000`; the API lives under `/api`. Storage
comes from `SELFEVALS_STORAGE_URL` / the global `--db` flag. Navigation:
workspace list → workspace overview → experiment detail (Iterations / Compare /
Funnel / Decisions tabs) → iteration drawer → trace viewer → thread viewer.
The Compare tab renders a server-side structured diff with a recommendation and
an honest holdout caveat (`unavailable` when no split was recorded).

## 6. When to hand off

- **Classify failures / grow the taxonomy** → **`error-analysis`** (needs
  `--persist-traces failed`): `analyze pull` → code → `analyze push` → human
  `failuremode promote`.
- **Baseline, regression gate, decisions, scale** → **`iterate-and-ship`**.

## What you must / must not do

- **Storage is Postgres.** Persisted runs use `SELFEVALS_STORAGE_URL` / the
  global `--db <postgres-url>`; `--no-persist` needs no DB. SQLite is only
  `migrate-sqlite`. Don't hand-edit the DB — all reads/writes flow through the
  CLI and `/api`.
- **`cache`, `funnel`, `failure_reasons` are diagnostics** — never decision gates.
- **`compare` needs both iterations from the same experiment.** Cross-experiment
  ids return 400 / the CLI refuses.
- **`min_recall` only relaxes `must_include`** (see `design-your-dataset`).
- **Don't reach for an unimplemented proposer** (`bayesian`/`bandit`/
  `evolutionary`) — use `manual`/`grid`/`random`/`llm_proposer`.
