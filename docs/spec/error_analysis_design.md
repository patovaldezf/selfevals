# Error Analysis + Failure-Mode Taxonomy — Design Doc

**Status**: Blueprint for the next implementation session. No code shipped yet.
**Owner**: pato
**Target session**: dedicated; ~800-1200 LOC across schema, storage, CLI,
aggregator, plus one bundled skill.

This doc translates "make experimentation robust — really see errors, failure
modes, annotations, patterns, and test hypotheses" into an implementation plan
tight enough that the next session can build it without re-doing the research.
The research that informed it is cited inline.

It covers **Loop B (continuous error analysis)** in full. **Loop A (onboarding /
repo-walking to seed evals + taxonomy)** is documented as out-of-scope here (§10)
with enough shape that it can be specced separately later — both loops read and
write the same taxonomy, so the taxonomy + handshake designed here is the shared
foundation.

---

## 1. What we're building

A closed loop, not a dashboard. The difference matters: a dashboard shows error
counts; a loop drives the next experiment. The loop:

```
experiment runs (cheap; no LLM clustering inline)
        │
        ▼
deterministic graders tag known failures  (already exists)
        │
        ▼  YAML opted in → selfeval leaves an analysis bundle ready
[external coding agent runs the `error-analysis` skill]
   1. selfeval analyze pull <experiment>   → JSON bundle (failed traces + live taxonomy)
   2. agent does open coding + axial coding against the EXISTING taxonomy
   3. selfeval analyze push <experiment> < result.json
        │
        ▼  selfeval persists: failure_mode_ids on outcomes, candidate modes in taxonomy
human promotes candidate modes → official   (selfeval failuremode promote)
        │
        ▼  proposer reads dominant modes → next iteration targets mode X
        ▼  compare verifies: did mode X shrink?
```

selfeval **never calls an LLM** for this. It owns the data, the contract, the
persistence, and the verification. The intelligence lives in an external agent
(Claude Code, the harness, any agent that honours the contract). This mirrors
the telemetry decision: consume the expensive/generic layer through a standard
rather than embedding it (see `sdk_otlp_design.md`).

### Why a skill, installed with the SDK

`pip install selfeval` installs a small set of bundled skills under a
discoverable path. The `error-analysis` skill is one of them. A coding agent
pointed at a selfeval repo can discover the skill, run the CLI handshake, and
do the categorization. The skill is **part of the product**, versioned with
selfeval, so the contract and the agent instructions never drift apart.

---

## 2. Grounding in established methodology (do not reinvent)

The error-analysis literature is settled. We follow it rather than invent.

**Open coding → axial coding** (Hamel Husain & Shreya Shankar, *AI Evals*):
a reviewer writes free-form notes on the *first* failure in each trace (open
coding), then groups those notes into a named, testable taxonomy (axial coding).
"An LLM can assist with the categorization step" but **human review is
mandatory**. Stop at theoretical saturation (~20 traces with no new category;
review ≥100). Source:
<https://hamel.dev/blog/posts/evals-faq/why-is-error-analysis-so-important-in-llm-evals-and-how-is-it-performed.html>

**Discover-once, classify-thereafter** (LangSmith Insights Agent): the decisive
design detail — "discovered top-level categories are automatically saved back to
the config, **but only if the config had no categories defined beforehand**."
First run discovers; later runs classify against the saved taxonomy. This is the
fix for taxonomy drift. Source:
<https://docs.langchain.com/langsmith/insights>

**Canonical seed vocabulary** (industry-common failure modes) for the initial
taxonomy so users aren't starting from zero: `groundedness_miss`,
`refusal_over_trigger`, `refusal_under_trigger`, `tool_call_arg_mismatch`,
`tool_call_wrong_tool`, `agent_loop`, `retrieval_miss`, `schema_violation`,
`hallucinated`.

### Decisions already made (do not re-litigate)

| Decision | Picked | Killed alternatives |
|---|---|---|
| Who clusters | **External coding agent via bundled skill** | Embedded LLM client in selfeval (deps, keys, cost, latency in the loop) |
| Handshake | **CLI `analyze pull` / `analyze push` (JSON)** | API endpoints (needs server), in-proc callback (couples runtime) |
| Taxonomy lifecycle | **Discover-once, classify-thereafter** | Re-cluster from scratch each run (drift), pure fixed taxonomy (blind) |
| Mode identity | **Stable `fm_…` entity id; agent classifies, never renames** | Free-string tags per run (no longitudinal tracking) |
| Candidate promotion | **Human gate (`failuremode promote`)** | Auto-promote on threshold (noisy taxonomy) |
| Taxonomy scope | **Per-workspace** | Global (cross-domain contamination), per-experiment (no reuse) |
| Cheap path | **Deterministic graders run first, unchanged** | LLM classifies everything (wasteful on known failures) |

---

## 3. The taxonomy: `FailureMode` entity

Workspace-scoped `BaseEntity` (reuses the existing storage + optimistic
concurrency machinery). This is the source of truth for "what failure modes
exist", with stable ids so the same mode can be tracked across every experiment
and iteration forever.

```python
# src/selfeval/schemas/failure_mode.py  (NEW)

class FailureModeStatus(StrEnum):
    CANDIDATE = "candidate"   # proposed by an agent, not yet human-confirmed
    OFFICIAL  = "official"    # confirmed; counts toward metrics & proposer input
    RETIRED   = "retired"     # kept for history, no longer assigned

class FailureModeExample(SelfEvalModel):
    trace_id: NonEmptyStr
    quote_pointer: str | None = None   # evidence snippet (payload-routed)
    quote_hash: str | None = None
    note: str | None = None            # the open-coding note that led here

class FailureMode(BaseEntity):
    _id_prefix: ClassVar[str] = "fm"
    slug: NonEmptyStr                  # human-stable handle, e.g. "invented_price"
    title: NonEmptyStr
    definition: NonEmptyStr            # the testable axial-coding definition
    status: FailureModeStatus = FailureModeStatus.CANDIDATE
    parent_mode_id: str | None = None  # hierarchy: subcategory → top-level
    examples: list[FailureModeExample] = Field(default_factory=list)
    proposed_by: NonEmptyStr           # "agent:claude-code" | "human:pato" | "seed"
    first_seen_iteration: int | None = None
    superseded_by: str | None = None   # when merged into another mode
```

`slug` is the human-stable handle; `id` is the machine-stable identity. Two
candidates that turn out to be the same mode get merged via `superseded_by`
(history preserved, never deleted).

---

## 4. The handshake — `selfeval analyze pull` / `push`

CLI-first, JSON over stdout/stdin, no server. This is the contract; get it right
and any agent honours it.

### `selfeval analyze pull <experiment_id> [--iteration N] [--only-failed]`

Emits a single JSON **AnalysisBundle** to stdout:

```jsonc
{
  "schema_version": "1.0.0",
  "workspace_id": "ws_…",
  "experiment_id": "exp_…",
  "iteration": 7,
  "taxonomy": [                       // the LIVE taxonomy — classify AGAINST this
    {"id": "fm_…", "slug": "invented_price", "title": "…",
     "definition": "…", "status": "official"}
  ],
  "traces": [                         // failed traces needing coding
    {"trace_id": "tr_…", "run_id": "run_…", "thread_id": "th_…",
     "eval_case_id": "case_…",
     "grade": {"label": "fail", "score": 0.0,
               "deterministic_modes": ["schema_violation"],   // already-tagged
               "judge_reason": "the bot quoted a price not in the catalog"},
     "transcript": [ {"role":"user","content":"…"}, … ],     // resolved messages
     "first_error_span": {"kind":"llm_call","name":"…","error":null}}
  ],
  "instructions_ref": "skill://error-analysis"    // where the method lives
}
```

Notes:
- Only traces graded `fail`/`error`/`partial` are included (`--only-failed`
  default true). Deterministic tags are passed in so the agent doesn't re-derive
  what's already known — it focuses on the *untagged* residue (open coding).
- `transcript` is the resolved `messages_in`/`messages_out` (the importer work
  already done) so the agent reads real text, not pointers.
- `first_error_span` operationalizes Hamel's "code the first failure" rule.

### `selfeval analyze push <experiment_id>` (reads JSON on stdin)

Accepts an **AnalysisResult**:

```jsonc
{
  "schema_version": "1.0.0",
  "assignments": [                    // trace → mode (existing id OR new slug)
    {"trace_id": "tr_…",
     "mode_id": "fm_…",               // set when it matched an existing mode
     "new_mode_slug": null,           // XOR: set when proposing a NEW candidate
     "open_note": "quoted a price absent from the catalog",
     "quote": "the X costs $499",     // evidence; selfeval payload-routes it
     "confidence": 0.82}
  ],
  "proposed_modes": [                 // axial coding: new candidates
    {"slug": "invented_price",
     "title": "Invented price",
     "definition": "States a concrete price not present in the provided catalog.",
     "parent_slug": "hallucinated"}   // optional hierarchy link
  ],
  "hypotheses": [                     // optional: feed the proposer
    {"targets_mode_slug": "invented_price",
     "statement": "Adding the catalog to the system prompt removes invented prices.",
     "suggested_parameters": {"system_prompt": "…"}}
  ]
}
```

selfeval's `push` handler, transactionally:
1. Creates `proposed_modes` as `status=candidate` (idempotent on `slug` within
   workspace; a repeat slug updates the existing candidate's examples).
2. Records each assignment: stamps the `failure_mode_id` onto the trace's
   `GraderResult.failure_modes` (resolving `new_mode_slug` → the candidate's id),
   payload-routes the `quote`, appends a `FailureModeExample`.
3. Stores `hypotheses` as `Proposal` seeds linked to the experiment (consumed in
   §7). It does **not** auto-run them.
4. Rejects an assignment that sets both `mode_id` and `new_mode_slug`, or
   neither (the XOR invariant — the agent classifies *or* proposes, never both).

**The classify-don't-rename rule is enforced here**: an assignment may only
reference an existing `mode_id` or a `new_mode_slug`. It can never edit the
`title`/`definition` of an existing mode. Renaming is a human action
(`failuremode edit`). This is what keeps mode identity stable across runs.

---

## 5. Fixing the persistence bug (foundation — do this first)

`docs/STATUS.md` admits: failure-mode counts live on a fresh
`OptimizationResult` but `IterationMetrics` does not carry them, so they don't
survive persistence. Until fixed, **nothing here is queryable historically.**

Change: add `failure_mode_counts: dict[str, int]` to `IterationMetrics`
(`schemas/iteration.py`), keyed by `failure_mode_id` (not free string). The
aggregator already computes the counter (`aggregator.py:148`) — switch its key
from the raw tag string to the resolved mode id, and persist it. `compare.py`
and `markdown.py` already render failure-mode diffs and tables gracefully when
present; they start showing real data once it persists.

This single change unlocks: "top modes of experiment X", "trend of mode Y across
iterations", "modes of thread Z" — all via the queries we already have
(`experiment_detail`, `anchor_set_history`, `load_thread`).

---

## 6. CLI surface (new commands)

```
selfeval analyze pull <exp> [--iteration N] [--only-failed/--all]   # emit bundle
selfeval analyze push <exp>                                          # ingest result (stdin)
selfeval failuremode list <ws> [--status candidate|official|retired]
selfeval failuremode promote <fm_id>      # candidate → official (the human gate)
selfeval failuremode retire <fm_id>
selfeval failuremode merge <fm_id> --into <fm_id>   # sets superseded_by
selfeval failuremode edit <fm_id> [--title …] [--definition …]
```

All follow the existing `_help.py` one-line + `Example:` epilog contract, and the
`SelfEvalUserError` (exit code 2) convention.

---

## 7. Closing the loop: modes → hypotheses → verify

The half that turns analysis into improvement, reusing existing parts:

- **Proposer input**: `ProposerInputs` already has `iterations_consulted`; add an
  optional `failure_modes_consulted: list[str]`. When an experiment has official
  modes with nonzero counts, the proposer (especially a future `llm_proposer`)
  receives the dominant modes + any pushed `hypotheses` as context, so a
  `Proposal.hypothesis` can explicitly say "reduce mode fm_…".
- **Verification**: `compare.py` already diffs failure-mode counts between two
  iterations. Once counts are keyed by stable id (§5), "did targeting mode X
  reduce it?" is a direct before/after on that id — no new machinery.
- **DecisionMatrix**: the existing `INVESTIGATE` outcome is the natural hook to
  flag "this iteration introduced a new candidate mode — analyze before keeping."

---

## 8. The `error-analysis` skill (bundled with the SDK)

Shipped under the package so `pip install selfeval` makes it discoverable. The
skill is thin — it encodes the *method*, not intelligence:

- **Preflight**: locate the selfeval CLI; identify the target experiment.
- **Pull**: run `selfeval analyze pull` → load the bundle.
- **Open coding**: for each failed trace, read the transcript + `first_error_span`,
  write a one-line note on the first failure. Skip traces already fully explained
  by a deterministic tag unless the residue suggests a deeper mode.
- **Axial coding against the live taxonomy**: for each note, decide — does it match
  an existing mode's `definition`? → assign that `mode_id`. Else → propose a
  `new_mode_slug` with a testable definition. **Never rename existing modes.**
- **Saturation check**: report when ~20 consecutive traces produced no new mode.
- **Push**: emit the `AnalysisResult` and run `selfeval analyze push`.
- **Hand back to human**: print which candidates are strongest (frequency +
  confidence) so the human can `failuremode promote` in a batch.

The skill's instructions explicitly cite the open/axial coding method (§2) so the
agent applies the established technique, not an ad-hoc one.

---

## 9. YAML opt-in — the senior design

The flag is **declarative and governable**, not a boolean afterthought. An
experiment spec opts in with a block that marks *and* configures:

```yaml
error_analysis:
  enabled: true
  taxonomy: workspace        # which taxonomy to classify against (only "workspace" in v1)
  trigger:
    when: fail_rate_above    # only stage a bundle when worth a human/agent's time
    threshold: 0.10
  scope: failed_only         # failed_only | all
```

Semantics: selfeval **stages an analysis bundle** (marks the experiment
`analysis_pending` and makes `analyze pull` return data) **only when the trigger
fires**. It never invokes an agent or an LLM. This is the senior choice because:
(a) it's declarative — the intent lives with the experiment, reviewable in the
diff; (b) the threshold prevents wasting analysis effort on healthy runs (the
same instinct as LangSmith's sample-size controls); (c) it keeps the hard
boundary that selfeval owns data + contract, agents own intelligence. Default
`enabled: false` keeps the loop fast for experiments that don't need it.

---

## 10. Out of scope (documented for later, not built now)

- **Loop A — onboarding / repo-walking.** A separate bundled skill where an agent
  reads the user's repo, infers what the agent-under-test does, proposes an eval
  structure, and **seeds the initial taxonomy**. It writes the same `FailureMode`
  entities (as `proposed_by: "agent:onboarding"`). Specced separately; this doc's
  taxonomy + push contract is deliberately the shared foundation it will reuse.
- **Auto-promotion of candidates.** Humans promote in v1 (Hamel: review is
  mandatory). A future threshold-based auto-promote can layer on top once trust
  in the agent's proposals is established.
- **Cross-workspace / shared taxonomies.** Per-workspace only in v1.
- **Inline clustering during the run.** Explicitly rejected (§2) — analysis is a
  deliberate, opt-in, post-hoc step so the loop stays cheap.
- **Embedded LLM judge re-clustering.** selfeval never calls an LLM here.
- **Web UI for the taxonomy + cluster explorer.** The queries expose the data;
  rendering it (hierarchical cluster view à la LangSmith Insights) belongs to the
  web session.
- **PII handling in the analysis bundle.** Transcripts leave selfeval to the
  agent; redaction policy is pending (shares the open question from
  `sdk_otlp_design.md` §10).

---

## 11. Acceptance criteria for the implementing session

Done when, on a clean machine:

1. `IterationMetrics.failure_mode_counts` persists and survives a round-trip; the
   markdown report and `compare` show real failure-mode data (closes the
   STATUS.md gap).
2. `FailureMode` entity + CRUD + the six `failuremode` CLI commands work, with
   the human promotion gate.
3. `selfeval analyze pull <exp>` emits a valid AnalysisBundle; `analyze push`
   ingests an AnalysisResult, enforces the XOR + classify-don't-rename invariants
   transactionally, and lands assignments + candidates + hypotheses.
4. The bundled `error-analysis` skill is discoverable after install and drives a
   full pull → code → push → promote cycle against the pingpong example.
5. A second `analyze pull/push` round on the same experiment classifies against
   the now-official modes (no duplicate candidates for the same failure) —
   proving discover-once, classify-thereafter.
6. All existing tests pass; new tests cover the push invariants, persistence
   round-trip, and the second-round stability property. `mypy --strict` clean,
   `ruff` clean. No new co-author trailers on commits.
```
