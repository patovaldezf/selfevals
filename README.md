# selfevals

Self-improving evals framework for AI agents.

Point selfevals at your agent and it runs a structured experiment: it
feeds eval cases through an adapter, grades each trace, sweeps the
parameters you expose, and renders a report that tells you which
configuration to keep. CLI-first, multi-tenant from day one, and agnostic
to the agent framework underneath — selfevals never calls your provider;
your agent does, and selfevals grades the result.

> Status: **v0.5.0 — runtime functional.** The CLI works end-to-end:
> load an experiment spec → run cases through an adapter → grade traces →
> persist iterations → render a report. Adapters and graders are async,
> with concurrent repetitions and grading. v0.5.0 adds **per-grader scoring**
> (optimize against one named grader instead of a conjunctive worst-of, and
> report each grader's own `pass@1`) and **proposer-aware convergence** (the
> grid proposer now enumerates its full cartesian product instead of
> early-stopping on a plateau). Both were surfaced by a real integration —
> see [Case study](#case-study-brain_os-dogfooding-its-own-memory) below.
> See [`docs/spec/`](docs/spec/) for the canonical and operational specs that
> drive design, and [`docs/STATUS.md`](docs/STATUS.md) for an honest
> what-works / what-doesn't snapshot.

## Install

```bash
pip install selfevals
```

The distribution is `selfevals`; the import name and the CLI command are
both `selfevals` (`import selfevals`, `selfevals --help`).

To run or trace an agent backed by a real provider, install the matching
**extra** — each one bundles the provider's SDK _and_ the tracing
integration, so a single install is enough:

```bash
pip install 'selfevals[openai]'      # or [anthropic], [bedrock], [vertex],
                                      #    [langchain], [crewai]
pip install 'selfevals[all]'         # every provider + the web API
```

The core install depends only on `pydantic` and `pyyaml`; no provider SDK
is pulled until you ask for an extra.

## 60-second quickstart

```bash
pip install selfevals
selfevals examples copy pingpong     # writes evals/ into the current dir
selfevals run evals/experiments/example_pingpong.yaml --no-persist
```

Expected output: a markdown report showing two iterations, the best one
selected, and a top failure-modes table — end-to-end in under a second
against the bundled `EmbeddedAdapter` echo agent. No API key needed.

To persist results to SQLite and inspect them afterwards (note: `--db` is a
**global** flag, so it goes _before_ the subcommand):

```bash
selfevals --db ./selfevals.sqlite run evals/experiments/example_pingpong.yaml
selfevals --db ./selfevals.sqlite experiment list <workspace_id>
selfevals --db ./selfevals.sqlite report <workspace_id> <experiment_id>
```

The `run` command prints the workspace and experiment ids you need for the
follow-up commands.

## Concepts

The five nouns you'll meet everywhere:

| Term               | What it is                                                                                                                                 |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------ |
| **EvalCase**       | One test: an input (a validated multi-turn `messages` conversation, or any opaque payload), the expected outcome, and which graders apply. |
| **Adapter**        | The bridge to your agent — embedded callable, CLI subprocess, or HTTP endpoint. selfevals calls _it_, never the provider directly.         |
| **Grader**         | Scores a trace. `DeterministicGrader` (rules: substrings, tools, JSON schema) or `LLMJudgeGrader` (a rubric-driven judge).                 |
| **Proposer**       | Picks the next parameter configuration to try — `manual`, `grid`, or `random`.                                                             |
| **DecisionMatrix** | Turns each iteration's metrics into a verdict: keep, reject, investigate, spawn sub-experiment, or require a tradeoff review.              |

An **experiment** is a YAML spec wiring these together; a **run** executes
it, producing **iterations** the reporter ranks.

## Try it with a real LLM agent

Two parallel examples live in [`examples/`](examples/) — same three eval
cases (sentiment classification, structured extraction, open-ended support
reply), same graders, same temperature sweep, differing only in the
provider call. Both fall back to deterministic fakes when the API key is
unset, so they're runnable offline.

**Anthropic** ([`examples/hello_llm/`](examples/hello_llm/)):

```bash
pip install 'selfevals[anthropic]'
export ANTHROPIC_API_KEY=sk-ant-...        # optional; falls back to a fake
uv run selfevals run examples/hello_llm/experiment.yaml --no-persist
```

**OpenAI** ([`examples/hello_openai/`](examples/hello_openai/)):

```bash
pip install 'selfevals[openai]'
export OPENAI_API_KEY=sk-...               # optional; falls back to a fake
uv run selfevals run examples/hello_openai/experiment.yaml --no-persist
```

Each combines a `DeterministicGrader` (sentiment + extraction) with an
`LLMJudgeGrader` (the open-ended reply). The `GridProposer` sweeps
`temperature ∈ {0.0, 0.5, 1.0}`; the report ranks them and the
`DecisionMatrix` selects the winner. Against the real models the coolest
temperature typically wins `pass@1` while warmer settings degrade on the
structured-output case.

See [`examples/README.md`](examples/README.md) for a walk-through of the
file layout and how to adapt them to your own agent.

> The example specs and datasets reference `examples.hello_*.agent`
> import paths, so they run from a **source checkout** (clone the repo).
> The pip-installable `selfevals examples copy pingpong` flow ships only
> the dependency-free pingpong example today.

## Adapters

selfevals ships three concrete `AgentAdapter` implementations so you can
point the loop at any agent:

- `EmbeddedAdapter` — a Python callable in-process. Best for quick tests.
- `CliCommandAdapter` — invokes a subprocess and reads JSON on stdout.
- `HttpEndpointAdapter` — POSTs each case to an HTTP endpoint and reads JSON.

See `src/selfevals/runner/adapters.py` for the contract and
[`docs/adapters.md`](docs/adapters.md) for usage examples, per-adapter
YAML/code snippets, and a comparison table.

## CLI reference

`selfevals --help` lists every command; `selfevals <command> --help` shows
its arguments. The surface:

| Command                                      | Purpose                                                                                                                                                       |
| -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `init <slug>`                                | Create a workspace and seed the default failure-mode taxonomy.                                                                                                |
| `run <spec.yaml>`                            | Run an experiment spec end-to-end.                                                                                                                            |
| `report <ws> <exp>`                          | Render a stored experiment as markdown (`--format json` for JSON; the JSON now includes per-iteration `cache` hit counts and deduplicated `failure_reasons`). |
| `compare <ws> <itr_a> <itr_b>`               | Diff two iterations side by side.                                                                                                                             |
| `estimate`                                   | Dry-run cost estimate for a search space × cases × reps.                                                                                                      |
| `workspace show <ws>`                        | Inspect a workspace.                                                                                                                                          |
| `experiment list/show <ws> [exp]`            | List or inspect experiments.                                                                                                                                  |
| `iteration list <ws> <exp>`                  | List recorded iterations.                                                                                                                                     |
| `analyze pull/push <ws> <exp>`               | The error-analysis handshake (see below).                                                                                                                     |
| `failuremode list/promote/retire/merge/edit` | Manage the failure-mode taxonomy.                                                                                                                             |
| `skills list / path <name>`                  | Locate the agent skills bundled with the install.                                                                                                             |
| `examples copy <name>`                       | Copy a runnable example into the current project.                                                                                                             |
| `serve`                                      | Run the HTTP API (and the web dashboard, if built) in one process.                                                                                            |

`--db <path>` is a global flag (default `./selfevals.sqlite`) and goes
before the subcommand.

## Running the API + dashboard (dev local)

selfevals ships an HTTP API (its own "LangSmith") and a SvelteKit dashboard.
Everything runs on localhost — no deploy required.

**API only** (FastAPI on `:8000`, needs the `web` extra for `uvicorn`):

```bash
uv sync --extra web
python -m selfevals.api --host 127.0.0.1 --port 8000 --db ./selfevals.sqlite
# health check + smoke
curl -s localhost:8000/api/health
curl -s localhost:8000/api/workspaces
curl -s localhost:8000/api/openapi.json | python3 -m json.tool | head
```

Docs live at `/api/docs`; the OpenAPI schema at `/api/openapi.json` (a typed
client can be generated from it). CORS already allows the Vite dev server on
`:5173`.

**API + dashboard together** — build the web bundle once, then `serve`:

```bash
cd web && npm ci && npm run build && cd ..
selfevals serve --host 127.0.0.1 --port 8000 --db ./selfevals.sqlite
```

`serve` starts the API and, when `web/build/index.js` exists, the dashboard
next to it (use `--no-web` for API-only). Add `--reload` for auto-reload in
development.

**Trace persistence + id contract.** By default only failing traces are kept
(`persist_traces: "failed"` in the spec). To navigate every case from the UI,
keep all traces — either set `persist_traces: "all"` in the spec, pass
`"persist_traces": "all"` in the run request, or export
`SELFEVALS_TRACE_SAMPLING=all` to force it process-wide (precedence: request >
env > spec). `GET /traces/{id}` accepts **either** a trace id (`tr_…`) or a run
id (`run_…`) and echoes both on the response, so case→trace links never have to
guess which id a given endpoint emits.

**Launch an experiment over HTTP** (non-blocking — returns `202` immediately
and runs in the background; poll the experiment to follow progress):

```bash
curl -s -X POST "localhost:8000/api/workspaces/<ws>/experiments/run" \
  -H 'content-type: application/json' \
  -d '{"spec_path": "evals/experiments/example_pingpong.yaml", "max_iterations": 2}'
# → {"experiment_id": "exp_…", "workspace_id": "<ws>", "state": "draft", ...}
# then poll until state == "completed":
curl -s "localhost:8000/api/workspaces/<ws>/experiments/<exp>" | python3 -m json.tool
```

The body accepts either `spec_path` (a YAML spec on the server) or `spec_inline`
(the spec as a JSON object, with cases embedded under `dataset.cases_inline`).
The path workspace is authoritative. See
[`docs/api_reference.md`](docs/api_reference.md) for the full contract.

### Error analysis (closed loop)

selfevals grows a per-workspace failure-mode taxonomy and drives the next
experiment from it — it never calls an LLM itself. `analyze pull` emits the
failed traces plus the live taxonomy; an external coding agent does the
open/axial coding and `analyze push`es the result back; a human promotes
candidate modes via `failuremode promote`. The bundled
[`error-analysis` skill](src/selfevals/.agents/skills/error-analysis/SKILL.md)
(discoverable via `selfevals skills list`) encodes the method.

## Case study: brain_os dogfooding its own memory

selfevals isn't theoretical — it's used in production to grade a real agent.

**brain_os** is a memory OS for AI agents: an append-only `event_log` of raw
evidence, slowly distilled into `pages` by a _dream worker_, exposed to any
agent (Claude Code, Codex, Cursor) over MCP. Its hardest problem is
**retrieval** — given a query, surface the right pages — so it points
selfevals at its own hybrid retriever (FTS5 keyword + named-entity + 1-hop
graph, fused with RRF).

The integration is real code: brain_os registers **5 deterministic graders**
that extend selfevals' `Grader` contract (`task_shape_match`,
`must_include_recall`, `must_not_include_violation`, `layers_overlap`,
`citation_grounding`) and runs a parameter sweep over its retrieval config.
On its golden set it measures **MRR 0.896 / Recall@8 1.0** (n=8 queries),
with a CI regression gate at MRR ≥ 0.80.

The interesting part is what the experiment found about **selfevals itself**.
Running the sweep surfaced two framework limitations:

1. The grid proposer was early-stopping on a plateau and never tried the
   remaining `chunking × vector_weight` combinations.
2. A conjunctive `pass@1` was masking each grader's individual signal.

Those two complaints became the two headline features of **v0.5.0**:
proposer-aware convergence and per-grader scoring. A self-improving evals
framework improved by the agent it was grading — and the experiment also did
its job, relocating brain*os's retrieval bottleneck to upstream task-shape
classification \_with evidence, not intuition*.

## Documentation

| Doc                                                        | What it covers                                                                                                                                                             |
| ---------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`docs/eval_config.md`](docs/eval_config.md)               | The YAML experiment spec: top-level keys, `EvalCase`/`Expected` fields (including recall-based `must_include` via `min_recall`), graders, agent transports, and proposers. |
| [`docs/api_reference.md`](docs/api_reference.md)           | The canonical HTTP API reference — every endpoint, response schema, and error codes.                                                                                       |
| [`docs/json_report_schema.md`](docs/json_report_schema.md) | The `report --format json` output shape, including the per-iteration `cache` and `failure_reasons` keys.                                                                   |
| [`docs/adapters.md`](docs/adapters.md)                     | Adapter contract and per-transport YAML/code snippets.                                                                                                                     |
| [`docs/FRONTEND.md`](docs/FRONTEND.md)                     | The web UI spec (views, endpoints, roadmap).                                                                                                                               |
| [`docs/STATUS.md`](docs/STATUS.md)                         | Honest what-works / what-doesn't snapshot.                                                                                                                                 |
| [`docs/deploy.md`](docs/deploy.md)                         | Deploying the API to Fly.io (Dockerfile + `fly.toml` + volume), and why a serverless host like Vercel does not fit.                                                        |

## Layout

```
src/selfevals/        # the SDK package
  schemas/            # Pydantic v2 entities + contractual validators
  storage/            # SQLite + filesystem object store (interface abstracted)
  trace/              # native SDK decorators + OTel importer
  runner/             # agent adapters + executor + sandbox modes
  graders/            # deterministic + LLM-judge + calibration
  optimization/       # OptimizationLoop + proposers (manual/grid/random)
  decision/           # decision matrix → DecisionRecord
  reporter/           # markdown + JSON reports
  analysis/           # error-analysis handshake (pull/push, bundles)
  cli/                # argparse entrypoint
examples/             # runnable examples (pingpong, hello_llm, hello_openai)
docs/spec/            # canonical + operational specs (source of truth)
tests/                # pytest, mirrors src/selfevals layout
```

## Development

```bash
uv sync --all-extras --dev        # venv + every extra + dev tooling
uv run pytest                     # tests
uv run mypy src/selfevals         # types (strict)
uv run ruff check .               # lint
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the test layout, the optional
telemetry/web extras some tests require, and PR conventions.

## License

Apache-2.0
