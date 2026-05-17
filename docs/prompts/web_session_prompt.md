# Web/UI Session — Onboarding Prompt for Claude Code

Copy everything below this line into a fresh Claude Code session opened in this repo (`/Users/patriciovaldez/Desktop/proyectos/mis_repos/boostrap`). The prompt is self-contained — the receiving session has no memory of the conversation that produced it.

---

## Mission

Build the **web UI + the user-side SDK + the OTLP receiver bridge** for an evals framework called `bootstrap`. The backend Python library is already shipped (390 tests passing, mypy strict + ruff clean). Your job is the two halves that turn it into a product people actually use:

1. **A web UI** where users see experiments, iterations, decisions, traces, failure clusters, and the longitudinal anchor-set view.
2. **A user-side Python SDK** (`bootstrap.init()`) that captures LLM telemetry from any agent with **one decorator/init call** and pipes it into the backend.
3. **An OTLP receiver** embedded in `bootstrap run` that bridges OpenInference spans into the existing `TraceRecorder`.

You'll find a detailed design doc at `docs/spec/sdk_otlp_design.md` — **read it before writing any SDK / OTLP code**. It is the result of a research turn and locks in the architectural decisions. Don't relitigate; implement.

## Read first (in this order)

1. `docs/spec/operational_spec_v0.1.md` — the canonical product/operational spec. §F.2.1 (plug-and-play SDK), §G.2 (reports: UI + agent-native), §G.4 (UI as primary output), §H (anchor-set comparison), §I (init → discover → build-dataset → run flow), §J.6 (Linear/tracker integration), §O.4 (smoke <3min).
2. `docs/spec/sdk_otlp_design.md` — locked design for the SDK + OTLP receiver. Sections 1-11.
3. `docs/spec/evals_framework.md` — the canon. Read §22 (repo layout) and §23 (CLI surface) at minimum.
4. `README.md` and `CHANGELOG.md` — what already shipped.
5. `src/bootstrap/` walk:
   - `schemas/` — Pydantic models. Everything else depends on these.
   - `storage/sqlite.py` + `storage/interface.py` — your data source for the UI.
   - `reporter/markdown.py` + `reporter/json_report.py` — there's an `OptimizationResult` → JSON renderer; the agent-native report and the UI consume similar shapes.
   - `cli/main.py` + `cli/commands.py` — observe what `bootstrap run` already does end-to-end.
   - `evals/experiments/example_pingpong.yaml` — example spec that runs out of the box.

## Backend contract — what's already available

- **A working `bootstrap run <yaml>` CLI** that loads an experiment spec, executes the OptimizationLoop, persists `IterationRecord` + `DecisionRecord` to SQLite, and prints a markdown/JSON report.
- **A SQLite database** at `./bootstrap.sqlite` (configurable via `--db`). Every entity is a row keyed by `(workspace_id, entity_type, id)` with a JSON payload column. The web UI reads from this directly via a thin Python query layer you'll add — **do not** add a separate ORM, just wrap `SQLiteStorage.open(ws_id).list_entities(...)`.
- **The reporter package** already emits a clean JSON shape (`bootstrap.reporter.render_json`) — the UI reuses this same shape; do not invent a parallel one.

What does NOT exist yet and you're building:
- A web frontend (any framework — your call, see Stack below).
- A small HTTP API in front of the SQLite db so the web can query without bundling SQLite into the browser.
- The user-side SDK (`bootstrap.init()`) per `docs/spec/sdk_otlp_design.md`.
- The OTLP receiver embedded in `bootstrap run` per the same design doc.

## Product vibe — non-negotiable

The user said: "stripe / airbnb / chatgpt / claude — minimalist, effective, todo lo que necesitas. Inspírate también de LangSmith y Mercury."

Translation:
- **Stripe**: trust, density without clutter, monospaced numbers, decisive defaults, generous whitespace where it counts.
- **Airbnb**: warm but quiet, soft shadows, rounded corners, photography-quality color choices, calm interaction states.
- **ChatGPT/Claude**: command-K everywhere, conversational empty states, the UI gets out of the way of the content.
- **LangSmith**: this is your closest competitor for the trace/iteration views. Look at how they visualize runs, spans, side-by-side prompt diffs, dataset rows. Match the information density; beat them on calm.
- **Mercury**: dashboards with numbers that breathe. Sparklines that don't shout. Tabular data that respects you.

Anti-patterns to avoid:
- Gradient hero sections.
- Emoji-as-icons.
- "AI-generated SaaS" gradients (purple → pink → blue).
- Tooltips that explain what icons mean (use words).
- Drop-down menus three levels deep.
- A sidebar that takes 25% of the viewport just to show 5 items.

## Stack recommendation (open to challenge if you have a strong reason)

- **Framework**: Next.js 15 (App Router, Server Components). Reason: SSR for the SEO-able marketing surface, RSC for the data-heavy app surface, single deploy.
- **Styling**: Tailwind v4 + shadcn/ui as the base. Then *override aggressively* — out-of-the-box shadcn looks like every other AI SaaS; the design pass is what makes it not.
- **Charts**: Recharts for the simple stuff, `visx` if you need to ship anything custom (parallel coordinates, side-by-side diffs).
- **Tables**: TanStack Table v8.
- **State**: TanStack Query for server state, Zustand for any UI-only state (small). No Redux.
- **API layer**: tRPC if you want type-safety end-to-end, otherwise plain REST with Zod-validated handlers. Either is fine — pick the one that ships faster.
- **Backend HTTP service** (the bridge to SQLite): FastAPI in the same repo (`src/bootstrap/api/`). Reuses the existing Pydantic models. ~300 LOC.
- **Auth**: skip for MVP — it's a localhost dev tool. Stub a user header.
- **Deployment**: Vercel for the web. The Python API runs as a sidecar (or as a Vercel Python function for the read-only endpoints, if you want zero ops).

## Page inventory — minimal viable web

These are the screens that make the product feel real. Build them in this order; ship after each is solid.

### 1. `/` — Project picker / landing for the app
Empty state when no workspaces exist. CTA: "Create your first workspace" (calls `bootstrap init` semantically). Otherwise: a list of workspaces with last-run timestamp and current health (% of recent experiments that landed `keep_candidate`).

### 2. `/[workspace]` — Workspace overview
- Headline: name, slug, member count.
- Three cards: **Recent experiments** (last 10, sortable), **Active failure clusters** (top 5 by size — pulls from `J.6` once that lands; placeholder list now), **Anchor-set health** (single sparkline of anchor pass@1 over the last N runs).
- Sidebar: Experiments / Datasets / Agents / Settings.

### 3. `/[workspace]/experiments/[id]` — Experiment detail
This is the page people will stare at. Three tabs:
- **Iterations**: a table — # / proposed parameters (collapsible chip per key) / primary metric / Δ vs running best / decision badge / rationale. Click a row → drawer with full `IterationRecord` (search_space, hypothesis, all metrics, link to traces).
- **Compare**: side-by-side picker (two iterations from the same experiment), shows prompt/param diffs and metric deltas. **No** cross-experiment compare here — that lives on a different surface (Anchor Set).
- **Decisions**: chronological list of `DecisionRecord` with outcome chip and rationale. Filter by outcome. Decision log = audit trail.

### 4. `/[workspace]/traces/[run_id]` — Trace inspector
Tree view of spans (AgentTurn → LLMCall → ToolCall → Retrieval → ...). For each LLMCall: token breakdown, latency, cost, full messages_in / messages_out with copy buttons, reasoning blocks if present. **Same density as LangSmith but calmer color palette**. Bonus: a "what was different about this trace vs the previous case" diff button.

### 5. `/[workspace]/datasets/[id]` — Dataset detail
Case list with the fields that matter: name, taxonomy badges (level, feature, source), expected snippet, # times graded, % pass historic. Sort by hardest. Click → case detail with all past grade results across iterations.

### 6. `/[workspace]/anchor-set` — Longitudinal view
The §H view. A single big chart: anchor pass@1 over time, every dot is an experiment. Hover for experiment name + date + Δ from previous. Drill down to a side-by-side of "anchor in experiment A vs anchor in experiment B" — case by case.

### 7. `/[workspace]/clusters` — Failure clusters (skeleton OK)
The §J.6 view. Empty state for MVP: "Cluster module ships in a later release. Click here to set up Linear integration when it lands." This screen is a placeholder but the design should reserve real estate so it doesn't feel bolted-on later.

### 8. Global: Command-K palette
Cmd-K opens: jump to experiment, jump to trace, jump to case, switch workspace, "run experiment". Every action in the app reachable via keyboard.

### 9. Empty marketing surface (`/`, no auth)
One-page minimum: tagline, three-line value prop, code snippet showing the `bootstrap.init()` + `bootstrap run` happy path, a CTA. Stripe-clean. Don't ship until the app surface above works — this can wait.

## Design tokens — starting point you can refine

```css
/* Spacing scale: 4 / 8 / 12 / 16 / 24 / 32 / 48 / 64 / 96 / 128 */
/* Use 4 only inside dense components. Default body spacing >= 16. */

/* Type scale (Inter + JetBrains Mono for numbers) */
--text-display: 36px / 1.05 / 600 letter-spacing -0.02em;
--text-h1:      24px / 1.2  / 600;
--text-h2:      18px / 1.3  / 600;
--text-body:    14px / 1.5  / 400;
--text-mono:    13px / 1.45 / 500 (JetBrains Mono);

/* Color (light mode first; dark mode is a deliberate second pass) */
--bg:        #FCFCFC;
--surface:   #FFFFFF;
--border:    #E8E8E6;
--text-1:    #0A0A0A;
--text-2:    #525252;
--text-3:    #888;
--accent:    #1F1F1F;     /* primary buttons; *not* a brand purple */
--success:   #0F7B3E;
--warning:   #B45309;
--danger:    #B91C1C;
--chart-1:   #1F1F1F;
--chart-2:   #6B7280;
--chart-3:   #B45309;
/* Avoid using accent color in charts. Charts stay grayscale + warning/danger. */

/* Radii */
--radius-sm: 4px;
--radius-md: 8px;
--radius-lg: 12px;

/* Shadows (Airbnb-inspired: barely there) */
--shadow-1: 0 1px 2px rgba(0,0,0,0.04), 0 1px 1px rgba(0,0,0,0.02);
--shadow-2: 0 4px 12px rgba(0,0,0,0.06);
```

Two fonts only. Inter for everything text, JetBrains Mono for numbers, IDs, code, traces, table cells with metric values. Tabular numerals enabled by default (`font-variant-numeric: tabular-nums`).

## Specific implementation notes

### Backend HTTP API (new — you build this)
- Location: `src/bootstrap/api/` (FastAPI app, mounted as a separate console script `bootstrap-api`).
- Endpoints (read-only for MVP):
  - `GET /workspaces`
  - `GET /workspaces/{ws_id}/experiments`
  - `GET /workspaces/{ws_id}/experiments/{exp_id}` → includes derived `OptimizationResult` JSON via existing `cli.commands._reconstruct_result`.
  - `GET /workspaces/{ws_id}/experiments/{exp_id}/iterations`
  - `GET /workspaces/{ws_id}/iterations/{iter_id}`
  - `GET /workspaces/{ws_id}/traces/{run_id}` → reads from filesystem object store (or whatever exists; the trace persistence story may need to be finished here too).
  - `GET /workspaces/{ws_id}/datasets/{ds_id}`
  - `GET /workspaces/{ws_id}/anchor-set` → aggregates anchor metrics across experiments.
- Writes for MVP: only `POST /workspaces` (create) and `POST /workspaces/{ws_id}/experiments` (queue a run). Everything else is read-only — the CLI is still how runs happen.

### SDK + OTLP receiver
Follow `docs/spec/sdk_otlp_design.md` to the letter. Sections 2 ("decisions already made") and 11 ("acceptance criteria") are the contract. If you find a reason to deviate, **document it before deviating** — that doc was the result of explicit research and pushback.

### Trust the existing tests
390 tests pass on `main`. Don't break them. Add new tests in the same style — pytest, type-annotated fixtures, `uv run pytest -q`.

### Commits
- One concern per commit, atomic.
- Subject line `<type>(scope): summary` (see existing commit history).
- **No** `Co-Authored-By: Claude…` trailers. The repo history was scrubbed of those; keep it that way.

## How to start

1. Read the four files in "Read first".
2. Spin up the existing CLI to make sure your local is healthy:
   ```bash
   uv sync
   uv run pytest -q
   uv run bootstrap run evals/experiments/example_pingpong.yaml --no-persist --max-iterations 2
   ```
3. Decide between tRPC and plain REST — write a one-paragraph note in `docs/web/decisions.md` with the choice and why. Same for any other stack decision that deviates from the recommendation above.
4. Scaffold the Next.js app at `web/`. Get the empty workspace shell rendering against a hand-coded mock workspace before connecting any real data.
5. Build the FastAPI bridge at `src/bootstrap/api/`. Get `/workspaces` returning real data from the SQLite db.
6. Ship the experiment-detail page first — it's the highest-value screen.
7. Then the trace inspector.
8. Then the SDK + OTLP receiver (per the design doc).
9. Then anchor-set, datasets, clusters skeleton, command palette.
10. Marketing page last.

## What "done" looks like

- Web app live at `localhost:3000`, connects to FastAPI at `localhost:8000`.
- All page-1-through-8 surfaces work against real data from `bootstrap run` outputs.
- The user-side SDK (`bootstrap.init()`) captures Anthropic + OpenAI calls and they appear in the trace inspector.
- A 30-second screen recording walks through: run an experiment in the CLI → open the web → see the iteration list → drill into the best iteration → see its trace.
- Tests: existing 390 + new tests for the API + SDK + receiver, target >450 total.
- mypy strict + ruff clean for new Python.
- Lighthouse on the marketing page >95.
- No co-author trailers anywhere.

## When to ask the user

Ask before:
- Adding a new heavyweight dep (anything >5MB or with native compilation).
- Choosing a paid SaaS for any part of the stack.
- Making auth real (the user explicitly said skip for MVP).
- Building anything not on the page inventory above (resist gold-plating).

Do NOT ask before:
- Reorganizing your own `web/` directory.
- Picking icon library, font weights, exact color shades within the palette.
- Adding a small dev dep (linter, testing util).
- Writing the FastAPI endpoints listed above.

## Final note

The user has high taste and low tolerance for slop. Every screen should pass the "would this look at home on stripe.com / airbnb.com / mercury.com" test. If a screen feels like it could be from a generic AI SaaS dashboard template, restart it. The minimalism is the product.

Now go.
