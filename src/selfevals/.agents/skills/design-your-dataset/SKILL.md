---
name: design-your-dataset
description: Decide WHAT to evaluate and HOW to grade it — write EvalCases and choose the right grader for each capability. Use when designing a dataset, picking a grader (deterministic, guardrail, set_match, confusion, funnel, judge_panel, llm_judge, trajectory, artifact_completeness), writing the `expected` block (must_include, min_recall, must_not_include, required/forbidden tools, structured_output, outcome, aliases), or managing datasets as first-class reusable entities (create, freeze, splits, ref vs inline). Use when the user says "what should I evaluate?", "which grader do I use?", "design test cases", or as the dataset step handed off from `evaluate-this-repo`. The full YAML key reference is `docs/eval_config.md`. For wiring the agent, use `connect-your-agent`; for running, use `run-eval-experiment`.
---

# Design your dataset (cases + graders)

A dataset is a set of **`EvalCase`s**. Each case is an input you feed the agent
plus what a pass must satisfy. Choosing the grader is choosing how "pass" is
measured. Full key-by-key reference: `docs/eval_config.md`. This skill is the
decision: what to put in a case, and which grader for which task.

## The EvalCase shape (JSONL, one per line)

```jsonl
{"name": "resolve two skus",
 "task_type": "product_resolution",
 "input": {"messages": [{"role": "user", "content": "find SKU42 and SKU7"}]},
 "context": null,
 "taxonomy": {"level": "final_response",
              "feature": {"primary": "commerce.product_resolution"},
              "source": {"type": "handcrafted"},
              "ground_truth": {"methods": ["exact_match"]},
              "dataset_type": "capability"},
 "expected": {"must_include": ["sku-42", "sku-7"],
              "must_not_include": ["sku-99"],
              "aliases": {"SKU42": "sku-42", "SKU7": "sku-7"},
              "outcome": "electronics"},
 "graders": ["rules", "detected_set", "category_class"]}
```

- `input` — the payload for the agent. A `messages` list is validated as a
  multi-turn conversation; any other dict is passed through verbatim.
- `context` — optional system info / retrieved docs.
- `taxonomy` — `level` / `feature` / `source` / `ground_truth` / `dataset_type`.
  Drives reporting and the failure-mode taxonomy.
- `expected` — consumed by the deterministic-family graders (see below).
- `graders` — names of the graders that score **this** case. A name resolves to
  a **registry grader** (`deterministic`, `guardrail`, `artifact_completeness`,
  `trajectory`, `set_match`) **or** to a grader you declared in the spec's
  top-level `graders:` block (where you give each a `name`). A name that's
  neither raises "Grader 'x' not registered".

### The `expected` block (deterministic-family fields)

- `must_include: list[str]` — substrings that must appear.
- `min_recall: float | None` — when set **and** `must_include` is non-empty,
  `must_include` is graded by **recall** (fraction present); PASS iff
  `recall >= min_recall`, score = recall. When `None` (default), `must_include`
  is all-or-nothing. **`min_recall` only relaxes `must_include`** — hard
  violations still force FAIL.
- `must_not_include: list[str]` — substrings that must be absent (hard rule).
- `required_tools` / `forbidden_tools: list[str]` — must be disjoint.
- `required_citations: list[str]`, `required_sections: list[str]` (artifacts).
- `structured_output: dict | None`, `output_schema: dict | None` — for
  structured graders.
- `outcome: str | None` — the true class (read by the `confusion` grader).
- `aliases: dict[str, str]` — normalize agent output to canonical tokens (e.g.
  `{"SKU42": "sku-42"}`) before set scoring.

Optional severity fields: `failure_weights` (per-severity weights) and
`critical_failure_modes` (zero-tolerance modes).

## Choose the grader — map task → grader

Every name below is a real grader (registered in `graders/registry.py` or
declared as a spec grader). Pick by what the agent *does*:

| Task / agent behavior                          | Grader                  | How it scores |
| ---------------------------------------------- | ----------------------- | ------------- |
| Hard rules (must say X, never Y, tool policy)  | `deterministic`         | `expected` checks; all-or-nothing unless `min_recall` |
| Content guardrails (forbidden/required regex, PII) | `guardrail`         | deterministic; a FAIL is **blocking** |
| Single-label classification                    | `confusion`             | NxN matrix + per-class F1, predicted vs `expected.outcome` |
| Detect/return a **set** of items               | `set_match`             | completeness / precision / recall / F1 of `structured_output["detected"]` vs `must_include` (via `aliases`) |
| Multi-stage pipeline (found→resolved→correct)  | `funnel`                | N sequential levels, gate short-circuits children |
| Subjective quality, open-ended                 | `judge_panel` / `llm_judge` | N judges + consensus / one judge vs a rubric |
| Tool-use / decision trajectory                 | `trajectory`            | multi-step / tool-call checks |
| Produces a structured artifact                 | `artifact_completeness` | schema validity + `required_sections` |

Two ways a grader enters a run:

1. **Registry grader** — referenced by bare name in a case's `graders:` list, no
   config (`deterministic`, `guardrail`, `artifact_completeness`, `trajectory`,
   `set_match`).
2. **Spec grader** — declared in the spec's top-level `graders:` block with a
   `type` + `name` + params; the case references it by `name`. Use this for
   `judge_panel` / `llm_judge` / `funnel` and for tuned `set_match`.

```yaml
graders:                              # top-level block in the spec
  - type: set_match
    name: detected_set
    params: { gating: completeness, threshold: 1.0 }
  - type: judge_panel
    name: quality_panel
    rubric: "Did the agent resolve every requested product?"
    n_judges: 3
    consensus: majority               # majority | unanimous | weighted
    judge_entrypoint: mypkg.judges:quality   # optional; falls back to the agent
  - type: confusion
    name: category_class
    params: { extract: category }
```

### The funnel grader (when scoring has stages)

A funnel composes N levels into one breakdown tree; a `gate: true` level that
fails marks its children SKIPPED. Each level has `key` (unique), `extract` (path
selector over `structured_output`: `""`, `foo`, `foo.bar`, `foo[]`,
`foo[].bar` — **no positional index**, use `by_index`), and `match` (a builtin
kind or `{grader: <name>}`). Builtin kinds: `exists`, `equals`, `by_key`,
`by_index`, `set_match`, `tool_called`, `span_exists`. The `tool_called` /
`span_exists` matches read the **trace** (the agent must emit `tool_uses`). The
runnable reference for every kind is `evals/experiments/example_showcase.yaml`
(`selfevals examples copy showcase`).

## Datasets as first-class entities

A dataset can be inline in the spec or a persisted, reusable entity.

```yaml
dataset:
  cases_path: ../datasets/myeval.jsonl   # inline: materialized at launch
  # OR
  ref: ds_01HXX...                       # ref: resolve a persisted Dataset
```

Persist and manage with the CLI (the canonical path shared by CLI/API/launch):

```bash
selfevals dataset create <ws> --from evals/datasets/myeval.jsonl --name golden-v1 --type golden
selfevals dataset list <ws> --status frozen
selfevals dataset show <ws> ds_01HXX...
selfevals dataset freeze <ws> ds_01HXX...   # recompute manifest hash, status=frozen (immutable)
```

Freeze a dataset you use as a regression baseline so it can't drift. A dataset's
`split_allocation` (train/holdout) flows into the loop, so the Compare tab can
report an honest holdout caveat.

## What to put in a first dataset

- **Small and high-signal.** 5–20 cases that cover the capabilities that matter,
  including the failure cases you expect — not hundreds of synthetic ones.
- **One or two graders per case** to start. Combine a hard floor
  (`deterministic`/`guardrail`) with a quality signal (`judge_panel`) only when
  the task is genuinely subjective.
- **Grow from real failures.** Run with `--persist-traces failed`, then use the
  `error-analysis` skill to turn failures into named modes and new cases.

## What you must / must not do

- **A case can only reference graders it can see** — registry names + the
  spec-declared names. Otherwise: "Grader 'x' not registered".
- **`min_recall` only relaxes `must_include`.** `must_not_include` and other
  hard rules still force FAIL.
- **`required_tools` and `forbidden_tools` must be disjoint** (validation error
  otherwise).
- **Structured graders need structured output.** `set_match`/`confusion`/`funnel`
  read `structured_output` (and traces); make sure the agent emits it
  (`connect-your-agent`).
- **Don't pad the dataset.** A sharp handful beats a noisy pile.
