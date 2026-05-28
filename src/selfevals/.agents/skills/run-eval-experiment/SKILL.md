---
name: run-eval-experiment
description: Author, run, and read a selfevals experiment end-to-end — write the YAML spec (cases, expected, graders, proposer, target), run it with the CLI, interpret the markdown/JSON report (metrics, funnel, cache, failure_reasons), and navigate the web UI (serve → workspace → experiment → iteration drawer → trace → thread viewer, with the Funnel and Compare tabs). Use when a human or agent wants to write/run/interpret a selfevals experiment or use its web UI. For classifying failures after a run, hand off to the `error-analysis` skill.
---

# Run an eval experiment (author → run → read → inspect)

You are driving selfevals end-to-end: turn an agent + a dataset into a structured
experiment, run it, and read the result. selfevals owns the loop (proposer →
agent → graders → decision → persistence); **your agent** is the thing under
test, and selfevals calls *it* — selfevals never calls a provider itself.

This guide is the practitioner's path. For the exhaustive field-by-field
reference, link out (those docs are being added in parallel, so the links may
land slightly after this skill — that's fine):

- `docs/eval_config.md` — every YAML key.
- `docs/json_report_schema.md` — every report key.
- `docs/api_reference.md` — the web/API surface.
- `docs/adapters.md` — how to wire embedded / CLI / HTTP agents (already present).

## 0. Preflight

- Confirm the CLI: `selfevals --help`. If the project uses `uv`, prefix every
  command with `uv run` (e.g. `uv run selfevals --help`). The README invokes it
  both ways; match what the project already uses.
- The `--db <path>` flag is **global** and goes *before* the subcommand
  (default `./selfevals.sqlite`): `selfevals --db ./selfevals.sqlite run ...`.
- Need a workspace? `selfevals init <slug>` creates (or idempotently re-opens)
  one and prints its id. The example spec carries a `workspace:` key, so for a
  first smoke run you can skip this and use `--no-persist`.
- Want a runnable starting point? `selfevals examples copy pingpong` writes an
  `evals/` tree into the current project.

## 1. Write an eval config (YAML)

A spec has four top-level blocks: `workspace`, `experiment`, `dataset`, `agent`.
Minimal working shape (mirrors `evals/experiments/example_pingpong.yaml`):

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
  proposer:
    strategy: grid
  search_space:
    model_params:
      level: [0.0, 1.0]
  run:
    sandbox: mock
    max_iterations: 4

dataset:
  cases_path: ../datasets/pingpong.jsonl   # or `cases_inline:` for a list

agent:
  entrypoint: selfevals.examples.pingpong:run   # legacy embedded shape
```

The `agent:` block is transport-tagged. Besides the legacy
`{entrypoint: "mod:fn"}` (embedded callable), you can declare
`{type: cli, command: [...]}` or `{type: http, url: "..."}` — see
`docs/adapters.md`.

### EvalCases and `expected`

Each case (`EvalCase`) carries `input` (the payload/messages fed to the agent),
`expected` (what a pass must satisfy), `taxonomy` (level / feature / source /
ground_truth / dataset_type), and `graders` (which graders score it).

`expected` is consumed by the deterministic graders. Key fields:
`must_include`, `must_not_include`, `required_tools`, `forbidden_tools`,
`required_citations`, `required_sections` (for artifact agents),
`structured_output` / `output_schema`, `outcome`.

The newer field is **`min_recall`** (optional float in `[0, 1]`):

- When set **and** `must_include` is non-empty, `must_include` is graded by
  **recall** — the fraction of required substrings present — instead of
  all-or-nothing. The grade **PASSES iff recall ≥ min_recall**, and the
  grader's `score` is the recall value, also exposed as `details["recall"]`.
- Hard violations still dominate: `must_not_include` (and other hard rules)
  force **FAIL even if recall passes**. Recall never rescues a hard violation.
- When `min_recall` is `None` (default), `must_include` stays all-or-nothing.

### Graders

Graders that are referenceable by string in a case's `graders:` list (resolved
through `selfevals.graders.registry`):

- `deterministic` — rule-based `expected` checks (must_include / min_recall /
  must_not_include / tools / schema).
- `guardrail` — deterministic content guardrails (forbidden/required regex,
  basic PII, double-value); a FAIL is blocking.
- `artifact_completeness` — schema validity + `required_sections` for
  artifact-producing agents (optional advisory LLM signal that never flips the
  verdict).
- `trajectory` — multi-step / tool-use trajectory checks.

`llm_judge` (`LLMJudgeGrader`) and `judge_panel` (`JudgePanelGrader`) ship too,
but they need constructor args (rubric, judge list), so they are **wired
programmatically (or via the spec's judge block / `judge_panel` config), not by
bare name in a case's `graders:` list.** Referencing an unregistered name raises
a friendly "Grader 'x' not registered" error listing the available names.

### Proposers

`experiment.proposer.strategy` selects how iterations are proposed. Implemented:
`manual`, `grid`, `random`, `llm_proposer`. (`bayesian` / `bandit` /
`evolutionary` are reserved and will raise "not implemented".) `grid` and
`random` walk `experiment.search_space`; `llm_proposer` lets a model propose the
next parameters.

## 2. Run an experiment

```bash
# Smoke run, no persistence, default report (markdown):
selfevals run evals/experiments/example_pingpong.yaml --no-persist

# Persisted run, capped iterations, JSON report, keep failed traces:
selfevals --db ./selfevals.sqlite run evals/experiments/example_pingpong.yaml \
    --max-iterations 4 --reps 3 --format json --persist-traces failed
```

`run` flags (verified via `--help`): `--workspace`, `--max-iterations`,
`--reps`, `--format {markdown,json}`, `--no-persist`,
`--persist-traces {none,all,failed}`. Persisted failed traces are what
`analyze pull` later feeds to error analysis.

Re-render a report from already-persisted iterations:

```bash
selfevals report <workspace_id> <experiment_id>            # markdown
selfevals report <workspace_id> <experiment_id> --format json
```

Diff two iterations of the **same** experiment by primary metric (note: the
args are **iteration ids**, plus the workspace — not experiment ids):

```bash
selfevals compare <workspace_id> <iter_a_id> <iter_b_id>
```

Other useful commands: `selfevals experiment list <ws>`,
`selfevals iteration list <ws> <exp>`, `selfevals estimate ...` (dry-run cost),
`selfevals skills list`.

## 3. Read the report

**Markdown** (default) is the human skim. **JSON** (`--format json`) is the
machine shape consumed by CI bots / dashboards (schema is versioned; see
`docs/json_report_schema.md`). Per-iteration keys worth knowing:

- `metrics` — `primary` ({name, value}), `guardrails`, `reliability`.
- `failure_modes` — counts per failure-mode id.
- `funnel` — per-grader breakdown of how cases flowed (drill-down source for
  the Funnel tab).
- `cache` — `{hits, llm_calls}`: cache hits vs actual LLM calls this iteration.
- `failure_reasons` — a **deduplicated** list of non-passing grader rationales:
  `[{grader, label, score, reason, failure_modes}]`. One entry per distinct
  `(grader, label, reason)` so the report stays compact; lets a consumer see
  *why* a grader failed without raw SQLite spelunking.

`cache`, `funnel`, and `failure_reasons` are **additive / informational** — they
describe the run, they never change the decision.

## 4. Use the web UI

`selfevals serve` starts the FastAPI bridge and (when a web build is present)
the SvelteKit UI in one process — no manual proxy:

```bash
selfevals --db ./selfevals.sqlite serve                 # auto-detects web build
selfevals serve --web-dist web/build --port 8080        # explicit build dir
selfevals serve --no-web                                # API only (headless)
```

Defaults: host `127.0.0.1`, port `8000`; the API lives under `/api`.

Navigation flow:

1. **Workspace list** (`/`) → pick a workspace.
2. **Workspace overview** (`/[ws]`) → experiments, datasets, traces, threads.
3. **Experiment detail** (`/[ws]/experiments/[experiment]`) with four tabs:
   **Iterations**, **Compare**, **Funnel**, **Decisions**.
4. **Iteration drawer** — click an iteration to see what the agent actually did.
5. **Trace viewer** (`/[ws]/traces/[trace]`) — one trace in full.
6. **Thread viewer** (`/[ws]/threads/[thread]`) — reached from a trace that
   carries a `thread_id` (the trace page links to it, with the turn position).

The three newest views:

- **Thread viewer** (`/[ws]/threads/[thread]`) — the multi-turn conversation a
  trace belongs to, reachable from any trace with a `thread_id`.
- **Funnel tab** — per-iteration grader breakdown with drill-down (built from
  the report's `funnel` key).
- **Compare tab** — a server-rendered structured diff of two iterations (the
  diff math has one source, server-side) with a **recommendation** plus an
  honest **holdout caveat**: when no holdout split is recorded it renders a
  first-class `unavailable` state rather than fabricating a number.

## 5. When to hand off to error-analysis

After a run, if you need to *classify* failures and grow the taxonomy (open
coding → axial coding), that is a separate, established method — do not improvise
it here. Run with `--persist-traces failed` (or set it in the spec) so failed
traces exist, then follow the sibling **`error-analysis`** skill
(`../error-analysis/SKILL.md`), which drives `analyze pull` → code →
`analyze push` → human `failuremode promote`.

## What you must / must not do

- **Do not hand-edit the SQLite DB.** All reads/writes flow through the CLI and
  the `/api` surface. Persistence is selfevals's job.
- **`cache`, `funnel`, and `failure_reasons` are additive and informational** —
  diagnostics only. They never change a decision; don't treat them as gates.
- **`compare` requires both iterations from the same experiment.** Cross-
  experiment ids are not apples-to-apples — the API returns 400 and the CLI
  refuses. The Compare tab's recommendation is advisory, and its holdout state
  is honest: `unavailable` means no split was recorded, not "passed".
- **Don't reference an unregistered grader by name.** Only `deterministic`,
  `guardrail`, `artifact_completeness`, and `trajectory` resolve from a case's
  `graders:` list; judges are wired programmatically.
- **`min_recall` only relaxes `must_include`.** Hard violations
  (`must_not_include`, etc.) still force FAIL; recall never overrides them.
- **Don't reach for an unimplemented proposer** (`bayesian` / `bandit` /
  `evolutionary`) — they raise. Use `manual` / `grid` / `random` / `llm_proposer`.
- **Don't duplicate error-analysis here.** Hand off to that skill for failure
  classification; this skill is about running and reading the experiment.
