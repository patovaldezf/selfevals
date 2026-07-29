---
name: evaluate-this-repo
description: Stand up evals for a codebase from scratch. Use when a human says "set up evals for this project", "evaluate this agent", "Claude, do evals", "I want evals", "how good is my agent?", or hands you a repo with no eval harness yet. This is the autonomous end-to-end driver: it inspects the repo to find the agent under test, decides WHAT is worth evaluating and HOW (which grader), wires the agent to selfevals (which adapter), designs a first high-signal dataset, scaffolds + runs the experiment, reads the result, and proposes the next move — delegating each step to the focused sibling skills (connect-your-agent, design-your-dataset, run-eval-experiment, error-analysis, iterate-and-ship). Start here for "make evals exist"; use the `selfevals` skill instead when the intent is already specific (run/analyze/iterate an existing eval).
---

# Evaluate this repo (autonomous eval bootstrap)

The user pointed you at a project and said, in effect, "make evals for this."
There is no harness yet. **You** drive the whole thing: read the repo, decide
what matters, wire it up, run it, read it, and recommend the next step. selfevals
runs the optimization loop and stores the evidence; the agent in *their* repo is
the thing under test; **selfevals never calls an LLM itself** — you and their
agent supply all the intelligence.

This skill is the orchestrator. Each numbered phase hands off to a focused
sibling skill for the deep mechanics; read that sibling when you reach its phase.

## 0. Preflight

- Confirm the CLI: `selfevals --help`. If the project uses `uv`, prefix every
  command with `uv run` (e.g. `uv run selfevals --help`). Match what the repo
  already uses.
- **Infra first: `run` needs Postgres + Redis + a live worker.** There is no
  ephemeral mode — `run` shards execution onto a queue, and without a worker
  draining it the command fails fast saying so. `docker compose up -d` starts
  all three; set `SELFEVALS_STORAGE_URL` (or pass `--db postgresql://…` as a
  global flag before the subcommand). The worker's `SELFEVALS_REDIS_URL` must
  match yours exactly, database number included. SQLite is legacy: only the
  one-shot `selfevals migrate-sqlite` import, never a live backend.
- Want a runnable skeleton to anchor on? `selfevals examples copy pingpong`
  (trivial echo loop, no key) or `selfevals examples copy showcase` (every
  grader type + funnel match kind, offline). Read the copied YAML — it is the
  ground-truth shape of a spec.

## 1. Inspect the repo — find the agent under test

Don't guess. Read the code first (use the `explore` skill or targeted reads).
You are answering five questions:

1. **What kind of agent/product is it?** Chatbot / multi-turn assistant, RAG
   pipeline, tool-using agent, single-label classifier, structured extractor,
   artifact generator (writes a doc/PR/email), router, etc. The kind decides the
   grader (phase 3).
2. **Where is its entrypoint?** A Python function (`mod:fn`), a CLI binary, an
   HTTP endpoint, or a class. This decides the adapter (phase 4).
3. **What are its inputs and outputs?** Free text? A `messages` list? Does it
   return prose, JSON (`structured_output`), tool calls, or an artifact? Look at
   the function signature / route schema / a sample call.
4. **What provider/model does it use?** Anthropic, OpenAI, Bedrock, local. This
   only matters for pricing + telemetry, and it's fine if it's "unknown".
5. **What does the team already care about?** Existing tests, a prompt file,
   issues, a README "known failure" list — these are free signal about what
   breaks and what "good" means.

Write down a one-paragraph model of the agent before continuing. If the repo has
no callable agent at all (just a prompt, or nothing), say so and offer to
scaffold one with `connect-your-agent` rather than inventing an eval for nothing.

## 2. Decide WHAT to evaluate

You are choosing a small set of **high-signal capabilities and risks**, not
"everything." Aim for the 3–6 behaviors where being wrong actually costs the
user something. Sources, in order of trust:

- What the user told you they care about (ask if unclear — see "Guiding the
  user" below).
- What the code/tests/prompt reveal the agent is *supposed* to do.
- The obvious failure surface for this agent kind (a RAG agent hallucinates /
  drops citations; a classifier confuses adjacent classes; a tool agent calls
  the wrong tool or skips a required one; an extractor misses required fields).

Name each as a feature (e.g. `commerce.product_resolution`,
`support.refusal_safety`). These become `taxonomy.target_features` in the spec
and the vocabulary `error-analysis` grows later.

## 3. Decide HOW to evaluate — map each capability to a grader

Hand off to **`design-your-dataset`** for the full grader catalog and the
case shape. The quick map (every name below is a real registered/spec grader):

| Agent does…                         | Grader to reach for           |
| ----------------------------------- | ----------------------------- |
| Single-label classification         | `confusion` (NxN + per-class F1) |
| Detect/return a *set* of things     | `set_match` (completeness/precision/recall/F1) |
| Multi-stage pipeline (found→resolved→correct) | `funnel` (N gated levels) |
| Subjective quality / open-ended     | `judge_panel` (N judges) or `llm_judge` |
| Hard rules (must say X, never say Y)| `deterministic` / `guardrail` |
| Tool-use / decision trajectory      | `trajectory`                  |
| Produces a structured artifact      | `artifact_completeness`       |

Most real evals combine two or three (e.g. `deterministic` for the hard floor +
`judge_panel` for quality). Don't over-grade a first pass; one or two graders
per case is plenty to start.

## 4. Decide what to INTEGRATE — wire the agent (adapter)

Hand off to **`connect-your-agent`**. Pick the transport from phase 1:

- In-repo Python callable → **embedded** (`agent: {entrypoint: "mod:fn"}`),
  fastest to iterate.
- A binary / other language → **cli** (`agent: {type: cli, command: [...]}`),
  JSON over stdio.
- A deployed/staging service → **http** (`agent: {type: http, url: "..."}`).

If the agent's signature doesn't already match the adapter contract, write a thin
shim that adapts `AdapterRequest` → the agent → `AdapterResponse`. Keep the shim
in the user's repo, next to their agent.

## 5. Design the first dataset

Hand off to **`design-your-dataset`**. Start tiny and high-signal: 5–20 cases
that cover the capabilities from phase 2, including the failure cases you expect.
Write them as JSONL (`evals/datasets/<name>.jsonl`). Don't manufacture hundreds
of synthetic cases up front — a sharp handful beats a noisy pile, and you'll grow
the set from real failures via `error-analysis`.

## 6. Scaffold + run the experiment

Hand off to **`run-eval-experiment`**. Author the spec YAML
(`evals/experiments/<name>.yaml`) tying together dataset + agent + graders +
target, then:

```bash
# First, estimate the cost before spending tokens:
selfevals estimate --cases <N> --space-size <S> --reps <R> --cost-per-call <USD>

# Smoke run (needs docker compose up -d first):
selfevals run evals/experiments/<name>.yaml

# Real run, keep failed traces for analysis:
selfevals run evals/experiments/<name>.yaml --persist-traces failed --format json
```

Read the report (markdown for skimming, `--format json` for machine parsing).

## 7. Read the result and propose the next move

- **Healthy?** Set a baseline and a regression gate so future changes can't
  silently regress → hand off to **`iterate-and-ship`**.
- **Failing?** Classify the failures and grow the taxonomy → hand off to
  **`error-analysis`** (needs `--persist-traces failed`). Then iterate on the
  agent/prompt/params and re-run.

Always end a bootstrap by telling the user, in plain language: what you set up,
what the first run showed, and the single most valuable next step.

## Guiding the user

Ask the **minimum** questions needed, then propose a plan before generating
files. Good questions when the answers aren't in the repo:

- "What does this agent need to get *right*? What's the worst thing it could do?"
- "What counts as a passing answer — exact match, contains key facts, or a
  judgment call?"
- "Roughly how much are you willing to spend per run?" (feeds `estimate`).

Don't interrogate. Two or three questions, then act — the user iterates on your
v1. If they gave you nothing, default to a `pingpong`/`showcase`-anchored
offline smoke so they have *something* running in minutes, and grow from there.

## What you must / must not do

- **Read the repo before proposing an eval.** The most common mistake is
  inventing a generic eval that doesn't match how the agent is actually built.
- **selfevals never calls an LLM.** It runs the loop and grades; the agent under
  test (theirs) and the intelligence (yours) do the rest.
- **Don't over-build the first pass.** A 10-case offline smoke that runs green
  is worth more than a perfect spec that never runs.
- **Hand off, don't duplicate.** The mechanics live in the sibling skills; this
  skill is the conductor. When you reach a phase, read its sibling.
- **Postgres, not SQLite.** Runs use Postgres / `SELFEVALS_STORAGE_URL` and a
  live worker; there is no DB-less mode. SQLite is only `migrate-sqlite`.
