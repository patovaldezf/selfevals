---
name: iterate-and-ship
description: Close the eval loop — decide what to change next, compare iterations, gate against regressions, and run at scale. Use when a run is done and you need to pick the next parameter/prompt/tooling change, diff two iterations, set or check a dataset baseline for CI (exit codes), read the decision outcome (keep_candidate/reject/investigate/…), tune convergence, or scale out with durable workers (Redis). Use when the user says "did it improve?", "compare these runs", "gate this in CI", "set a baseline", "run this at scale", or as the iteration/ship step handed off from `run-eval-experiment`/`error-analysis`. Status snapshot: `docs/STATUS.md`.
---

# Iterate and ship (decide → compare → gate → scale)

A single run tells you where you are. This skill is about moving forward: picking
the next change, proving it helped, and locking the gain so it can't silently
regress. selfevals records the evidence and the decision; **you** choose what to
change, and **a human** owns the ship gates (baseline, promote).

## 1. Decide what to change next

The proposer drives the search. `experiment.proposer.strategy`:

- `manual` — you set the params; one iteration per explicit point.
- `grid` — exhaust `experiment.search_space` combinations (raises
  `SearchSpaceExhaustedError` when done).
- `random` — sample the search space.
- `llm_proposer` — a model proposes the next parameters (consults stored
  error-analysis hypotheses; it does **not** run them automatically).
- `bayesian` / `bandit` / `evolutionary` are **reserved and raise "not
  implemented"** — don't reach for them.

When error-analysis has named the failure modes, the highest-leverage next change
is usually the one targeting the dominant mode (a prompt/grounding fix, a tool
constraint), not blind hyperparameter sweeping.

## 2. Compare iterations

```bash
selfevals compare <workspace_id> <iter_a_id> <iter_b_id>
```

Args are **iteration ids** (+ workspace), and both must belong to the **same
experiment** (cross-experiment ids are refused — not apples-to-apples). The web
Compare tab renders the same diff server-side, with a recommendation and an
honest holdout caveat: `unavailable` means no holdout split was recorded, not
"passed". Record a `split_allocation` on the dataset (see `design-your-dataset`)
to get a real holdout number.

## 3. Read the decision outcome

Each iteration carries a `DecisionOutcome` (the engine's routing call):
`keep_candidate`, `reject`, `revert`, `feature_flag`, `investigate`,
`require_tradeoff_review`, `spawn_subexperiment`. It reflects the target +
guardrails in the spec. Guardrail FAILs (e.g. a `guardrail` grader, or a target
`guardrails:` entry like `cost_usd_per_case <= 0.05`) are blocking — they can force a
reject even when the primary metric improved.

## 4. Baseline + regression gate (lock the gain, gate CI)

The first run that completes on a dataset auto-registers its best iteration as
that dataset's baseline (idempotent — a later better run does not move it).
Inspect or re-baseline explicitly:

```bash
selfevals baseline show <ws> --dataset ds_01HXX...
selfevals baseline set  <ws> --dataset ds_01HXX... --iteration itr_01H...   # human raises the bar
```

Gate an iteration against the baseline — the **CI hook**:

```bash
selfevals regression check <ws> --dataset ds_01HXX... --iteration itr_01H... \
    --primary-drop 0.0 --f1-drop 0.05 --error-rate-rise 0.0
```

Exit codes: **`0`** ok, **`1`** regression, **`2`** usage error. It flags drops
in the primary/pass@1 metric, drops in any per-class confusion F1, and
(optionally) error-rate rises. Wire `regression check` into CI to block merges
that regress the eval. Freeze the dataset (`dataset freeze`) so the baseline
can't drift under it.

## 5. Tune convergence

`experiment.run.convergence` (`min_delta`, `patience`) stops the loop early when
iterations stop improving — useful for `grid`/`random`/`llm_proposer` runs so you
don't burn the whole budget chasing a flat curve. `max_iterations` is the hard
cap.

## 6. Run at scale (durable workers)

For real volume, runs execute as durable background jobs off a Redis stream
rather than in-process. Bring up Postgres + Redis (`docker compose up -d postgres
redis`), point both API and worker at the **same** Redis DB, then:

```bash
selfevals worker runs        # consume durable run jobs (SELFEVALS_REDIS_URL)
selfevals worker sweeper     # reap stranded jobs after a worker crash
```

`--once` processes a single job and exits (handy in tests/CI). Within a single
run, `experiment.run.parallelism` (default 8, `ge=1 le=64`) is the in-flight
ceiling — the loop fans out cases concurrently up to it; `rate_limit`
(`requests_per_minute`) is the real limiter when you hit provider tiers, and the
two compose (min wins). The retry layer (`run.retry`) handles transient 429/5xx.

> Gotcha: the worker and API must point at the **same Redis DB** or jobs sit in
> draft with no error. The launcher only picks Redis when `SELFEVALS_REDIS_URL`
> is in the API's env.

## 7. The human ship gates

selfevals proposes; the human disposes. The gates that are human-owned by design:
`baseline set` (raising the bar), and `failuremode promote` (candidate → official,
see `error-analysis`). Your output is a recommendation, not the ship decision.

## What you must / must not do

- **`compare` needs same-experiment iteration ids.** Cross-experiment is refused.
- **Regression check exit code IS the gate** — `0`/`1`/`2`. Don't parse the prose;
  read the exit code in CI.
- **Don't move a baseline silently.** `baseline set` is a deliberate human action.
- **Don't use unimplemented proposers** (`bayesian`/`bandit`/`evolutionary`).
- **Same Redis DB for worker + API**, or durable runs stall silently.
