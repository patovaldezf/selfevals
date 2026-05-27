---
name: error-analysis
description: Analyze an experiment's failed traces and grow its failure-mode taxonomy. Use when a bootstrap experiment has staged error analysis (or a human asks "why is this experiment failing?"). Drives the pull → open coding → axial coding → push → promote cycle. bootstrap owns the data and the contract; you provide the intelligence (the coding), it never calls an LLM itself.
---

# Error Analysis (open + axial coding)

You are the intelligence half of bootstrap's error-analysis loop. bootstrap has
already run the experiment, tagged the deterministic failures, and (if the YAML
opted in and the trigger fired) staged a bundle. Your job is to read the failed
traces and **grow the failure-mode taxonomy** so the next experiment can target
specific, named modes.

This is the established **open coding → axial coding** method (Hamel Husain &
Shreya Shankar's error analysis for LLM apps). Apply it faithfully — not an
ad-hoc clustering.

The hard boundary: **bootstrap owns data + contract + persistence; you own the
coding.** You never edit the database directly — everything flows through
`analyze pull` / `analyze push`. bootstrap never calls an LLM; you do all the
reading and judgement.

## 0. Preflight

- Confirm the `bootstrap` CLI is available: `bootstrap --help`. If the project
  uses `uv`, prefix commands with `uv run`.
- Identify the target **workspace id** and **experiment id**. If the user gave
  a spec path, the workspace is the spec's `workspace:` key; the experiment id
  is printed when the experiment was created/run.
- You will need the db path the project uses (the CLI's `--db` flag, default per
  the project). Reuse whatever the human/other commands already use.

## 1. Pull the bundle

```bash
bootstrap --db <db> analyze pull <workspace_id> <experiment_id> > bundle.json
```

`bundle.json` contains:
- `taxonomy`: the **live failure modes** (id, slug, title, definition, status).
  This is what you classify against. Treat OFFICIAL modes as the stable
  vocabulary; CANDIDATE modes are proposals awaiting a human.
- `traces`: each failed trace with `grade` (label, score, any
  `deterministic_modes` already tagged, optional `judge_reason`), the
  `transcript` (the real conversation), and `first_error_span` (bootstrap's
  guess at where it first went wrong).

If `traces` is empty, the run was healthy or nothing was staged — report that
and stop. Don't invent failures.

## 2. Open coding — one note per first failure

For **each** failed trace, in order:
1. Read the `transcript` and `first_error_span`. Find the **first** thing that
   went wrong (Hamel's rule: code the first failure, not the downstream cascade).
2. Write a single, concrete, one-line note describing *what* failed — behavioral,
   not a fix. Good: "cited a price the catalog never contained." Bad: "should
   validate prices" (that's a fix, not an observation).
3. If a `deterministic_modes` tag already fully explains the failure, you may
   skip writing a new note — **unless** the residue suggests a deeper mode the
   deterministic rule missed. Don't re-discover what's already tagged.

Keep notes verbatim-grounded: capture a short `quote` from the transcript that
evidences the failure. You'll attach it to the assignment.

## 3. Axial coding — classify against the LIVE taxonomy

This is the discipline that makes the taxonomy stable ("discover once, classify
thereafter"). For each note:

- **Does it match an existing mode's `definition`?** → assign that mode by
  `mode_id`. Prefer an existing mode whenever the definition genuinely fits.
- **No existing mode fits?** → propose a `new_mode_slug` with a **testable
  definition** (a sentence a different person could apply to a new trace and
  agree on). Lower-case, snake_case slug.
- **Never rename or redefine an existing mode.** If an official mode's
  definition is wrong, note it for the human — do not silently fork it.

Each trace gets **exactly one** of `mode_id` *or* `new_mode_slug` (XOR). bootstrap
rejects an assignment that sets both or neither.

## 4. Saturation check

Track new modes as you go. When ~20 consecutive traces produce **no** new mode,
you've reached saturation — the taxonomy now covers this run. Note it; you can
stop proposing and just classify the remainder.

## 5. Optional: hypotheses

For the dominant modes, you may add a `hypotheses` entry: a testable statement
("tightening the price-grounding instruction will reduce `invented_price`") with
optional `suggested_parameters`. bootstrap stores these; the proposer consults
them next iteration. It does **not** run them automatically.

## 6. Push the result

Emit an `AnalysisResult` JSON and push it:

```bash
bootstrap --db <db> analyze push <workspace_id> <experiment_id> --by "agent:<your-name>" < result.json
```

`result.json` shape:

```json
{
  "proposed_modes": [
    {"slug": "invented_price", "title": "Invented price",
     "definition": "Agent states a price not present in the catalog context."}
  ],
  "assignments": [
    {"trace_id": "trc_…", "mode_id": "fm_…", "quote": "…", "open_note": "…"},
    {"trace_id": "trc_…", "new_mode_slug": "invented_price", "quote": "$499", "open_note": "…"}
  ],
  "hypotheses": [
    {"targets_mode_slug": "invented_price",
     "statement": "Add an explicit 'only cite catalog prices' instruction.",
     "suggested_parameters": {"prompt_section": "grounding"}}
  ]
}
```

bootstrap validates the whole result **before** writing (transactional intent),
enforces the XOR and classify-don't-rename invariants, creates candidate modes
idempotently (re-proposing an existing slug does not duplicate it), and stamps
`mode_id` onto each trace's grader results. It prints a summary.

## 7. Hand back to the human

Print which candidates are strongest — frequency (how many traces) plus your
confidence — so the human can batch-promote:

```bash
bootstrap --db <db> failuremode list <workspace_id> --status candidate
bootstrap --db <db> failuremode promote <workspace_id> <fm_id>
```

Promotion (candidate → official) is a **human gate** by design. Never promote on
your own. Your output is a recommendation, not a decision.

## What you must not do

- Do not call any database or storage API directly. Only `analyze pull/push`
  and `failuremode *`.
- Do not rename, redefine, or merge existing modes (merging is a human
  `failuremode merge`).
- Do not promote candidates.
- Do not invent failures for healthy traces or pad the taxonomy to look busy —
  a smaller, sharper taxonomy is the goal.
