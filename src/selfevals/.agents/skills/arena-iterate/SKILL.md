---
name: arena-iterate
description: Drive a selfevals Arena — a bake-off between N git-branch code variants of one agent (different prompts, different providers like AssemblyAI vs ElevenLabs, workflow vs tool-calling agent, different tool sets) run in parallel on the same dataset. Use when a human wants to compare code variants empirically ("try both approaches and tell me which wins", "set up an arena for X vs Y"), or when an Arena already exists and needs its next round. selfevals owns orchestration (worktrees, parallel runs, scoring); you own the code changes and the judgment about what variant to try next.
---

# Arena Iterate (code-variant bake-off)

You are the intelligence half of selfevals's Arena loop. An Arena runs N named
code variants — each pinned to a git branch/commit in the target repo — against
the same dataset and graders, **in parallel**, by checking each one out into
its own git worktree. selfevals never edits code. You do: you read the results,
form a hypothesis, create a new branch with a code change, register it as a
variant, and launch the next round.

The hard boundary: **selfevals owns orchestration + scoring + worktrees; you
own the code.** You never touch git worktrees directly (selfevals manages
`.selfevals/worktrees/` — treat that tree as read-only, generated state) and
you never call the graders yourself — everything flows through the Arena API.

This skill assumes a selfevals API server is reachable (`selfevals serve`, or
whatever URL the human gives you — default `http://localhost:8000`). There is
no dedicated `selfevals arena` CLI yet, so every step below is a `curl` call
against the JSON API; substitute `jq` or Python for parsing as convenient.

## 0. Preflight

- Confirm the API is reachable: `curl -s $API/api/health`.
- Identify the **workspace id**, the **target repo path** (the git repo that
  contains the agent's source code — must be a real, already-committed repo;
  Arena checks out *commits*, not working-tree edits), and the **arena id**
  (if one already exists — ask the human, or `GET .../arenas` to list).
- If no Arena exists yet, create one (step 1). If one exists, skip to step 2.

## 1. Create the Arena (only if one doesn't exist yet)

You need: what the human wants compared (the `goal`), how the agent is
invoked (`agent_command` — an argv run per case, e.g.
`["python", "agent.py"]` — selfevals sets its `cwd` to each variant's
worktree automatically), and a `spec_template` — the same JSON shape as a
normal selfevals experiment spec (`dataset`, `graders`, `run`, `experiment`
target/decision blocks), **minus** the `agent:` block, which Arena injects
per variant. See the `run-eval-experiment` and `design-your-dataset` skills
for how to write `dataset`/`graders` — reuse that knowledge here verbatim.

```bash
curl -sX POST "$API/api/workspaces/$WS/arenas" -H 'content-type: application/json' -d '{
  "name": "tts-bakeoff",
  "goal": "cheapest TTS provider with acceptable latency on the voice-agent eval set",
  "repo_path": "/abs/path/to/target/repo",
  "agent_command": ["python", "agent.py"],
  "objective_metric": "pass_rate",
  "spec_template": { "experiment": {...}, "dataset": {...}, "graders": [...] },
  "budget": {"max_rounds": 5, "max_variants": 8}
}'
```

Returns `{"id": "arn_...", "state": "draft", ...}` — this is your `ARENA_ID`
for every step below.

## 2. Register variants

Each variant is an existing git ref (branch, tag, or commit) in `repo_path` —
**you create the branch first**, outside selfevals, then hand selfevals the
ref name. selfevals resolves it to a commit SHA immediately (so a typo fails
fast) and checks out a worktree + runs an optional `setup_command` (e.g.
`["uv", "sync"]`) in the background.

```bash
# In the target repo: make your code change on a fresh branch, commit it.
git -C /abs/path/to/target/repo checkout -b arena/elevenlabs <base-ref>
# ... edit the TTS provider call, prompt, tool set, whatever the variant is ...
git -C /abs/path/to/target/repo commit -am "arena: try ElevenLabs TTS"

curl -sX POST "$API/api/workspaces/$WS/arenas/$ARENA_ID/variants" -H 'content-type: application/json' -d '{
  "name": "elevenlabs",
  "git_ref": "arena/elevenlabs",
  "setup_command": ["uv", "sync"],
  "hypothesis": "ElevenLabs has lower TTFB than AssemblyAI on our voice cases"
}'
```

Returns 202 with `state: "preparing"`. Poll `GET .../variants` until it flips
to `ready` (or `failed` — check `error` and fix the branch/setup_command)
before launching a round. On the **first** round of a fresh Arena, register
every variant you want to compare (including a "baseline"/"main" variant if
the human wants one) before moving to step 3.

## 3. Launch a round

A round runs every `ready` variant in parallel — one child experiment per
variant, on the same dataset+graders. Omit `variant_ids` to run all
ready variants; pass a subset to re-run only some.

```bash
curl -sX POST "$API/api/workspaces/$WS/arenas/$ARENA_ID/rounds" -H 'content-type: application/json' -d '{}'
```

Returns 202 with a `round_id` and one entry per variant (each with its own
child `experiment_id`). Poll until every entry's status is terminal:

```bash
curl -s "$API/api/workspaces/$WS/arenas/$ARENA_ID/rounds/$ROUND_ID" | jq .state
# "running" -> ... -> "completed"
```

## 4. Pull the bundle — this is where you think

```bash
curl -s "$API/api/workspaces/$WS/arenas/$ARENA_ID/bundle" > bundle.json
```

The bundle gives you everything you need to decide the next move, without
re-deriving it yourself:

- `leaderboard`: every scored variant ranked by `objective_metric`, with
  `delta_vs_best`.
- `pairwise_vs_best`: for each non-winning variant, a structured diff against
  the current best (`metrics_diff`, `only_best`/`only_this` failure modes,
  `recommendation_kind`) — the same math `selfevals compare` uses, so read it
  the same way you'd read a compare report.
- `exemplar_failures`: a handful of real failed traces per variant
  (`grade_label`, `first_error_span`) — read these before guessing why a
  variant lost. Don't theorize from the leaderboard number alone.
- `variants[].history`: each variant's `primary_value`/`cost_usd` across past
  rounds, so you can see whether a variant is trending up or has plateaued.
- `arena.budget_rounds_remaining` / `budget_variants_remaining`: respect
  these — do not register a variant or launch a round that would exceed them;
  tell the human instead.

## 5. Decide: converge, or try something new

For each round, do ONE of:

- **Mutate a promising variant**: form a hypothesis from the pairwise diff +
  exemplar failures (e.g. "the losing variant's failures are all
  `missing_tool_call` — the workflow needs a retrieval step before the final
  answer"), branch off that variant's `resolved_sha` (not `main`), make the
  code change, register it as a new variant (step 2, `hypothesis:` explains
  *why*), and launch a fresh round including it alongside the still-live
  contenders.
- **Kill clearly losing variants**: don't re-run a variant that's been
  decisively behind for 2+ rounds with no improving trend in `history` — pass
  only the remaining `variant_ids` in the next round's launch, saving budget.
- **Stop and recommend**: when the leaderboard has been stable (same winner,
  `delta_vs_best` roughly flat) for 2+ consecutive rounds, or the budget is
  nearly exhausted, stop proposing new variants.

Never loop forever. If you can't converge within budget, say so honestly and
hand back your best-so-far leaderboard rather than silently stopping.

## 6. Recommend (never merge)

When you've converged (or run out of budget), promote the winner. This marks
the arena `completed` and returns copy-paste git/PR commands — **it does not
touch git**. Merging agent-authored code without human review is a decision
for the human, not you.

```bash
curl -sX POST "$API/api/workspaces/$WS/arenas/$ARENA_ID/promote" -H 'content-type: application/json' -d '{
  "variant_id": "var_..."
}'
```

Report the winner, its margin over the runner-up, the key failure modes that
separated them (from `pairwise_vs_best`), and the suggested commands — then
stop. Do not run `git merge` or open the PR yourself unless the human
explicitly asks you to.

## What you must not do

- Do not edit files inside `.selfevals/worktrees/` directly — that tree is
  generated by `ensure_worktree`; edits there are lost on the next round and
  never reach the source branch. Always edit in a normal clone/checkout and
  commit, then register the resulting ref.
- Do not fabricate a `resolved_sha` or skip registering a variant before
  launching a round that references it — an unready variant is rejected.
- Do not merge, push, or open a PR for the winner without explicit human
  instruction — `promote` only recommends.
- Do not exceed `budget.max_rounds` / `budget.max_variants` — check the
  bundle's `budget_*_remaining` before every new variant or round.
- Do not run a round with zero ready variants, or re-launch a round index
  that already completed (each round is append-only — launch a new one).
