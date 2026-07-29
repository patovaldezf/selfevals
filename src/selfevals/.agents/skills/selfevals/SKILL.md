---
name: selfevals
description: Orientation + decision map for the selfevals framework — what it is, the mental model, the full eval lifecycle, and which focused skill to use for a given intent. Use this when you need to get your bearings in selfevals, when the user's eval intent is specific (run / analyze / iterate / connect / design) and you need to route to the right skill, or when you need the canonical model of how the loop works and what the CLI/web surface offers. For the autonomous "set up evals for this repo from scratch" workflow, use `evaluate-this-repo` instead. Reference docs live in `docs/` (eval_config, json_report_schema, api_reference, adapters, STATUS).
---

# selfevals — orientation & decision map

selfevals is a self-improving, AI-native eval framework for agents. It runs
experiments (search space × cases × reps), grades each run concurrently, decides
what to keep, persists the evidence, and reports — then closes the loop by turning
failures into a growing failure-mode taxonomy.

This skill is the index. When the intent is already specific, route to the focused
skill below. When the user just says "set up evals for my project," use
**`evaluate-this-repo`** (the autonomous bootstrap) instead.

## The mental model (read once)

- **selfevals runs the loop; your agent is what's evaluated.** The loop is
  proposer → invoke the agent (via an adapter) → grade → decide → persist.
- **selfevals NEVER calls an LLM itself.** The agent under test makes the model
  calls; the intelligence in analysis (error coding, judgment) is *you*. selfevals
  owns the data, the contract, and persistence.
- **Authoring is YAML.** There is no `config/` — `evals/experiments/*.yaml`
  hydrates to a typed spec. The runnable references are `pingpong` (smallest) and
  `showcase` (every grader/match kind), via `selfevals examples copy <name>`.
- **Storage is Postgres, and it is required.** `SELFEVALS_STORAGE_URL` / the
  global `--db <postgres-url>`. `run` also shards execution, so it needs Redis
  **and a live worker** — `docker compose up -d` brings up all three. There is
  no ephemeral/in-memory mode. SQLite is gone: `selfevals migrate-sqlite` only
  imports a legacy file.
- **Humans own the ship gates.** `failuremode promote` and `baseline set` are
  deliberate human actions; agents recommend, humans dispose.

## The lifecycle

```
inspect → decide WHAT to evaluate → decide HOW (grader) → wire the agent (adapter)
   → design the dataset → author + run the experiment → read the report
   → analyze failures → iterate → baseline + regression gate → ship
```

## Decision map — route to the right skill

| The user / intent is…                                       | Use this skill        |
| ----------------------------------------------------------- | --------------------- |
| "Set up evals for this project" / no harness yet (do it all)| **evaluate-this-repo** |
| "What should I evaluate?" / "which grader?" / design cases  | **design-your-dataset** |
| "Connect my agent" / "evaluate my endpoint" / adapter shim  | **connect-your-agent** |
| "Run this eval" / write a spec / read the report / dashboard| **run-eval-experiment** |
| "Why is it failing?" / classify failures / grow taxonomy    | **error-analysis**     |
| "Did it improve?" / compare / gate CI / baseline / scale    | **iterate-and-ship**   |

## The CLI surface (one line each)

Authoring/running: `init`, `run`, `report`, `compare`, `estimate`, `serve`.
Datasets: `dataset create|import|list|show|freeze`. Iteration/inspection:
`experiment list|show`, `iteration list`, `workspace show`. Ship gates:
`baseline show|set`, `regression check`. Failure loop: `analyze pull|push`,
`failuremode list|promote|retire|merge|edit`. Scale: `worker runs|sweeper`.
Meta: `skills list|path|sync`, `examples copy`, `demo`, `migrate-sqlite`.

Global: `--db <postgres-url>` goes **before** the subcommand. Run `selfevals
<command> --help` for exact flags — every subcommand has a copy-paste example.

## The web UI (collaboration surface)

`selfevals serve` runs the FastAPI bridge + (when built) the SvelteKit UI in one
process. The human reviews runs and operates the gates there: experiment detail
(Iterations / Compare / Funnel / Decisions), failure-mode taxonomy (promote /
retire / merge / edit), datasets (create / freeze), trace + thread viewers with
live SSE, and metrics. The agent operates the same surface via `/api` (and the
CLI). See `docs/api_reference.md`.

## Starting from zero, manually

```bash
docker compose up -d                                # Postgres + Redis + worker
selfevals examples copy pingpong                    # smallest loop (no API key needed)
selfevals run evals/experiments/example_pingpong.yaml
selfevals examples copy showcase                    # every grader + match kind
selfevals demo --fresh                              # seeded end-to-end (real LLM)
```

## What you must / must not do

- **Route, don't reimplement.** This skill points; the focused skills do. For the
  full autonomous bootstrap, use `evaluate-this-repo`.
- **selfevals never calls an LLM.** Keep that model straight when reasoning about
  what the framework can/can't do.
- **Postgres, not SQLite** for persistence; SQLite is only `migrate-sqlite`.
- **Verify against the code, not memory** — `docs/STATUS.md` is the honest
  snapshot of what works; `selfevals <cmd> --help` is the source of truth for
  flags.
