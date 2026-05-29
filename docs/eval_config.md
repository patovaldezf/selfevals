# Eval Config Reference (YAML)

An experiment is a single YAML file (by convention under
`evals/experiments/*.yaml`) that wires together: an **experiment** contract,
a **dataset** of eval cases, an **agent** adapter, and optionally explicit
**graders**. The loader (`src/selfevals/repo/loader.py`) deliberately keeps
the YAML keys 1:1 with the field names on the underlying Pydantic entities
(`Experiment`, `EvalCase`, …), so Pydantic does all the shape validation —
there is no separate YAML-only DSL.

Run a spec with:

```bash
selfevals run evals/experiments/example_pingpong.yaml --no-persist
# or persist to SQLite (note: --db is a GLOBAL flag, before the subcommand):
selfevals --db ./selfevals.sqlite run evals/experiments/example_pingpong.yaml
```

See also: [`api_reference.md`](api_reference.md) (HTTP API),
[`json_report_schema.md`](json_report_schema.md) (the `--format json`
output), and [`adapters.md`](adapters.md) (adapter usage).

## Top-level keys

| Key | Required | Notes |
|-----|----------|-------|
| `workspace` (or `workspace_id`) | yes* | Workspace id. *Can be supplied via `--workspace` instead of the file. |
| `experiment` | yes | The experiment contract (mapping → `Experiment`). |
| `dataset` | yes | Where the eval cases come from. |
| `agent` | yes | The adapter that calls your agent. |
| `graders` | no | Explicit grader configuration. When omitted, cases reference graders by name (see [Graders](#graders)). |

---

## `experiment`

The keys mirror the `Experiment` schema (`src/selfevals/schemas/experiment.py`).
The loader auto-fills `id` and `workspace_id` when absent.

| Key | Type | Notes |
|-----|------|-------|
| `name` | string | Human name. |
| `goal` | string | One-line intent. |
| `mode` | enum | `handoff` (cloud-only parameter sweep against pinned artifacts) or `agent_loop` (writes code in your repo). |
| `taxonomy` | mapping | `target_features` (non-empty list), optional `target_levels`, `dataset_types`. |
| `datasets` | mapping | e.g. `optimization: { id: ds_..., version: 1 }`. |
| `target` | mapping | `primary: { name, operator, value }` and optional `guardrails: [...]`. |
| `editable` | mapping | Bool flags for what the proposer may change: `prompt`, `model_params` (default true), `model_choice`, `tool_code`, `workflow_graph`, `skills`. |
| `frozen` | mapping | Pinned `fleet`, `agents`, `datasets`. |
| `proposer` | mapping | `strategy` (`manual` \| `grid` \| `random`), `allow_search_space_expansion` (default false). |
| `search_space` | mapping | Parameter spaces the proposer samples, e.g. `model_params: { level: [0.0, 1.0] }`. |
| `run` | mapping | `sandbox` (`mock` \| `dry_run` \| `live_sandboxed` \| `live_canary`), `max_iterations` (1–10000, default 20), `convergence: { min_delta, patience }`, sampling. |
| `reliability` | mapping | `metrics: [...]` — each must match `pass@N`, `pass^N`, or a known reliability metric name. |
| `error_analysis` | mapping | Opt-in continuous error-analysis loop: `enabled`, `taxonomy: workspace`, `trigger: { when, threshold }`, `scope`. |

---

## `dataset`

Provide **exactly one** of:

- `cases_inline:` — a YAML list of case mappings (each is an `EvalCase`), or
- `cases_path:` — a path (relative to the YAML file) to a JSONL file, one
  `EvalCase` object per line.

```yaml
dataset:
  cases_path: ../datasets/pingpong.jsonl
```

The loader errors if neither (or both) is present, or if the dataset yields
zero cases.

### EvalCase

Each case is an `EvalCase` (`src/selfevals/schemas/eval_case.py`). The
loader fills `id` and `workspace_id` when absent. Core fields:

| Key | Required | Notes |
|-----|----------|-------|
| `name` | yes | Non-empty. |
| `task_type` | yes | Non-empty free-form label. |
| `modalities` | no | List of `text` (default) / `image` / `audio` / `voice` / `browser_use` / `sensor`; unique. |
| `input` | yes | The payload fed to the agent. When it carries a `messages` key it is validated as a typed multi-turn conversation; otherwise it is an opaque dict passed to the adapter verbatim. |
| `context` | no | Optional extra context dict. |
| `expected` | no | The pass criteria — see [Expected](#expected). |
| `taxonomy` | yes | Case classification — see below. |
| `graders` | no | List of grader names that apply to this case (unique). |
| `failure_weights` | no | `{ failure_mode: weight }`, weights ≥ 0. |
| `metadata` | no | `owner`, `tags`, `pii_status` (default `raw`), `approved_raw_by/at`, `notes`. |
| `blocking` | no | `{ merge: bool, release: bool }`. |
| `holdout` | no | When true, reserved for held-out eval (invisible to proposers). |

`taxonomy` (a `CaseTaxonomy`):

| Key | Notes |
|-----|-------|
| `level` | `single_step` \| `multi_step` \| `final_response` \| `step_level` \| `tool_call` \| `retrieval` (and others). |
| `feature` | `{ primary: str, secondary: [str] }` — `primary` required; must not also appear in `secondary`. |
| `source` | `{ type: handcrafted \| production \| staging \| development \| failure \| synthetic, ... }`. |
| `ground_truth` | `{ methods: [...] }` — non-empty, unique; e.g. `exact_match`, `schema_validation`, `rubric`. |
| `dataset_type` | exactly one of `smoke` \| `golden` \| `regression` \| `capability` \| … |
| `runtime` | defaults to `offline`. |

> **PII contract:** a case whose `source.type` is `production`/`staging`
> with `metadata.pii_status: raw` must also set `approved_raw_by` AND
> `approved_raw_at`, or validation fails.

A minimal case (matching the bundled `pingpong.jsonl`):

```json
{"name": "say pong", "task_type": "echo",
 "input": {"messages": [{"role": "user", "content": "ping"}]},
 "taxonomy": {"level": "final_response",
              "feature": {"primary": "commerce.product_resolution"},
              "source": {"type": "handcrafted"},
              "ground_truth": {"methods": ["exact_match"]},
              "dataset_type": "capability"},
 "expected": {"must_include": ["pong"]}}
```

### Expected

The declarative pass criteria consumed by the `DeterministicGrader`
(`Expected` in `eval_case.py`). All fields are optional:

| Field | Type | Behavior |
|-------|------|----------|
| `outcome` | string \| null | Free-form expected outcome label. |
| `must_include` | list[str] | Every substring must appear in the final response (case-insensitive by default). |
| `min_recall` | float \| null, [0, 1] | **Recall mode for `must_include`.** See below. |
| `must_not_include` | list[str] | None of the substrings may appear. |
| `required_tools` | list[str] | Every tool must be invoked in the trace. |
| `forbidden_tools` | list[str] | No tool may be invoked. (Must be disjoint from `required_tools`.) |
| `required_citations` | list[str] | |
| `policy_flags` | list[str] | |
| `structured_output` | object \| null | When set, the `DeterministicGrader` requires the adapter's structured output to equal this exactly. Also the **escape hatch** for custom expected fields — see below. |
| `output_schema` | object \| null | |
| `required_sections` | list[str] | Top-level keys an artifact must carry (consumed by `ArtifactCompletenessGrader`). |

#### `structured_output` as the escape hatch for custom expected fields

`Expected` has a **closed schema** (`extra="forbid"`): putting a field on
`expected:` that isn't in the table above raises a validation error. That is
intentional — it keeps the declarative contract tight.

When your **custom grader** needs domain-specific expectations
(`task_shape`, `must_include_slugs`, `layers_required`, …), put them inside
`structured_output` (a free-form object) and read them in your grader via
`context.case.expected.structured_output`:

```yaml
# in a case
expected:
  structured_output:
    task_shape: decision
    must_include_slugs: [acme, runway]
    layers_required: [self_model, options]
```

```python
# in your custom grader
async def grade(self, context):
    exp = (context.case.expected.structured_output or {})
    want = exp.get("task_shape")
    ...
```

Note: if you also set top-level `structured_output` for the built-in
`deterministic` grader's exact-match check, that same object is what your
custom grader reads — design the two uses to coexist (or use distinct
graders/cases).

#### `min_recall` (recall-based `must_include`)

By default `must_include` is **all-or-nothing**: every substring must be
present or the grade is FAIL with score `0.0`.

When `min_recall` is set (and `must_include` is non-empty), the
`must_include` dimension is graded by **recall** — the fraction of required
substrings present:

- `recall = (present substrings) / (total must_include)`.
- The grade is **PASS** iff `recall >= min_recall`, and **score = recall**.
- `recall` is exposed in the grade's `details["recall"]`.
- Missing substrings still emit their `missing_required_substring` failure
  mode (so diagnostics survive) but no longer force a FAIL on their own —
  the threshold decides.
- **Precedence:** hard violations (`must_not_include`, `required_tools` /
  `forbidden_tools`, `regex_match`, `structured_output`) *always* take
  priority. Even when recall clears the threshold, any hard violation makes
  the grade FAIL.

```yaml
expected:
  must_include: ["refund", "policy", "timeframe", "contact"]
  min_recall: 0.75            # PASS if at least 3 of the 4 appear
  must_not_include: ["guarantee"]   # but this still hard-fails if present
```

---

## `agent`

Selects which adapter the CLI wires up. Two YAML shapes are accepted.

**Legacy / embedded** — a Python callable in-process:

```yaml
agent:
  entrypoint: selfevals.examples.pingpong:run   # "module.path:callable"
```

**Tagged** — explicit transport via `type`:

```yaml
# Embedded (same as legacy):
agent:
  type: embedded
  entrypoint: my_pkg.agent:run

# CLI subprocess (argv list, reads JSON on stdout):
agent:
  type: cli
  command: ["python", "my_agent.py"]
  env: { MY_FLAG: "1" }          # optional, string→string
  timeout_seconds: 30            # optional, positive

# HTTP endpoint (POSTs each case, reads JSON):
agent:
  type: http
  url: "http://localhost:9000/run"
  headers: { Authorization: "Bearer x" }   # optional
  timeout_seconds: 30                       # optional
```

`type` must be one of `embedded` / `cli` / `http`. A `cli` or `http` agent
must not carry an `entrypoint`. See [`adapters.md`](adapters.md) for the
adapter contract.

---

## Graders

Cases reference graders by name in `EvalCase.graders`. The names resolve
through the grader registry (`src/selfevals/graders/registry.py`). The
built-in registered names are:

| Registered name | Class | What it does |
|-----------------|-------|--------------|
| `deterministic` | `DeterministicGrader` | Declarative rules from `Expected`: `must_include` (with optional `min_recall`), `must_not_include`, `required_tools`/`forbidden_tools`, `regex_match`, `structured_output`. |
| `artifact_completeness` | `ArtifactCompletenessGrader` | Checks an artifact carries each `Expected.required_sections` key with a non-empty value. |
| `guardrail` | `GuardrailGrader` | Guardrail-style pass/fail checks. |
| `trajectory` | `TrajectoryGrader` | Diagnostic checks over the tool-call / decision *sequence* (wraps an output grader). |

> Additional grader classes exist in `src/selfevals/graders/`
> (`LLMJudgeGrader`, `JudgePanelGrader`) but are **not** auto-registered by
> name; they are configured explicitly. The `judge_panel` grader is not yet
> exposed through the YAML `graders:` block.

### Custom graders

You can supply your own grader without modifying selfevals. Two ways:

**1. Dotted path (recommended — declarative, no side effects).** Reference
your grader class directly in a case's `graders:` list as
`"package.module:ClassName"` (note the `:`). It's imported on demand and
instantiated — no registration call needed:

```yaml
# in a case
graders:
  - deterministic                       # built-in, by registered name
  - my_pkg.graders:TaskShapeGrader      # custom, by dotted path
```

```python
# my_pkg/graders.py
from selfevals.graders.base import GradeLabel, Grader, GradeResult


class TaskShapeGrader(Grader):
    name = "task_shape"

    async def grade(self, context):
        ...
        return GradeResult(grader=self.name, label=GradeLabel.PASS, reason="...")
```

The class must subclass `Grader` and be **constructible with no required
arguments** (give any `__init__` parameters defaults). A built-in name and a
dotted path can be mixed freely in the same `graders:` list.

**2. Programmatic registration.** Call
`selfevals.graders.registry.register_grader(name, factory)` at import time and
then reference the grader by `name`. Registration is idempotent (last write
wins). Use this when you want a short stable name instead of a dotted path, but
it requires that your module is imported before grader resolution — the dotted
path avoids that ordering concern.

### The `graders:` block

The top-level `graders:` list configures graders the loader instantiates
directly. Each entry is a mapping. The loader currently supports two
`type`s: `deterministic` and `llm_judge`.

```yaml
graders:
  - type: deterministic
    name: rules                 # optional; defaults to the type name

  - type: llm_judge
    name: rubric_judge
    rubric: "Was the reply empathetic and factually accurate?"
    judge_entrypoint: my_pkg.judge:run   # optional for embedded agents;
                                         # REQUIRED for cli/http agents
```

Rules for the block:

- `type` must be `deterministic` or `llm_judge`.
- `name` defaults to `type`; names must be unique within the block.
- `llm_judge` requires a non-empty `rubric`. Its `judge_entrypoint`
  (`"module:callable"`) is optional only when the agent is `embedded`
  (it falls back to the agent's entrypoint); `cli`/`http` agents must name
  one explicitly.

---

## Proposers

`experiment.proposer.strategy` selects how the next parameter configuration
is chosen. MVP implements:

| Strategy | Behavior |
|----------|----------|
| `manual` | You enumerate the configurations to try. |
| `grid` | Cartesian sweep over `search_space` (e.g. `model_params: { level: [0.0, 1.0] }`). |
| `random` | Random samples from `search_space`. |

`allow_search_space_expansion` defaults to `false`.

---

## A complete example

The bundled `evals/experiments/example_pingpong.yaml`:

```yaml
workspace: ws_01HZZZZZZZZZZZZZZZZZZZZZZZ

experiment:
  name: pingpong baseline
  goal: warm up the end-to-end loop with a trivial echo agent
  mode: handoff
  taxonomy:
    target_features:
      - commerce.product_resolution
    dataset_types:
      - capability
  datasets:
    optimization: { id: ds_pingpong, version: 1 }
  target:
    primary: { name: pass@1, operator: ">=", value: 0.5 }
  editable:
    prompt: true
    model_params: true
  frozen:
    fleet: { id: flt_demo }
    agents:
      - { id: ag_demo }
    datasets:
      - { id: ds_pingpong }
  proposer:
    strategy: grid
  search_space:
    model_params:
      level: [0.0, 1.0]
  run:
    sandbox: mock
    max_iterations: 4
    convergence:
      min_delta: 1.0e-6
      patience: 10
  reliability:
    metrics:
      - pass@1
  error_analysis:
    enabled: true
    taxonomy: workspace
    trigger:
      when: fail_rate_above
      threshold: 0.10
    scope: failed_only

dataset:
  cases_path: ../datasets/pingpong.jsonl

agent:
  entrypoint: selfevals.examples.pingpong:run
```
