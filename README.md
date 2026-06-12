# selfevals

Self-improving evals for AI agents.

Point `selfevals` at your agent. It runs the agent on a dataset, grades the
traces, sweeps the parameters you allow, persists the result, and tells you
which configuration to keep.

It does not call your model provider. Your agent does. `selfevals` is the
measurement layer around it.

## What It Does

- Runs eval cases through an embedded Python function, CLI command, or HTTP endpoint.
- Grades outputs with deterministic rules, set matching, trajectory checks, guardrails, or LLM judges.
- Sweeps `manual`, `grid`, `random`, or offline hypothesis proposals.
- Captures traces, tokens, cost, tool calls, structured output, and failure modes.
- Persists experiments to SQLite and renders Markdown or JSON reports.
- Serves a FastAPI bridge and optional Svelte dashboard for live runs, cases, traces, and results.
- Exports failed traces for external error analysis, then ingests taxonomy updates back into the workspace.

Current version: `0.9.0`.

## Why This Exists

Most agent evals stop at "did it pass?" That is not enough.

For agents, the useful loop is:

1. Run the agent against real cases.
2. See exactly where it failed.
3. Try a constrained change.
4. Measure the new behavior.
5. Keep, reject, investigate, or split the experiment.

`selfevals` packages that loop into a CLI, SDK, API, storage layer, and report format.

## Install

```bash
pip install selfevals
```

The package, import, and CLI are all named `selfevals`:

```bash
selfevals --version
python -c "import selfevals; print(selfevals.__version__)"
```

Core install stays small: `pydantic`, `pyyaml`, and `httpx`.

Provider extras install both the provider SDK and the OpenInference tracing
adapter:

```bash
pip install 'selfevals[openai]'
pip install 'selfevals[anthropic]'
pip install 'selfevals[bedrock]'
pip install 'selfevals[vertex]'
pip install 'selfevals[langchain]'
pip install 'selfevals[crewai]'
pip install 'selfevals[all]'
```

The web/API extra:

```bash
pip install 'selfevals[web]'
```

Scale/storage extras:

```bash
pip install 'selfevals[postgres]'
pip install 'selfevals[redis]'
```

SQLite remains the default. For the local Postgres + Redis runtime profile,
use the repository `.env` values:

```bash
docker compose up -d postgres redis
set -a && source .env && set +a
selfevals serve --no-web
selfevals worker runs
```

`SELFEVALS_STORAGE_URL` points at the local Postgres container. `SELFEVALS_REDIS_URL`
points at the local Redis container on `localhost:6380`.

> **Worker and API must share the same `SELFEVALS_REDIS_URL`, DB number
> included.** Sourcing `.env` for both (as above) guarantees it — don't pass a
> partial `--redis-url` to the worker. If they land on different Redis DBs the
> job enqueues but nothing consumes it and the experiment stays stuck in
> `draft`. See [docs/deploy.md](docs/deploy.md#redis-run-workers) for the
> troubleshooting line.

## Quickstart

No API key. No model call. Runs the bundled pingpong eval.

```bash
pip install selfevals
selfevals examples copy pingpong
selfevals run evals/experiments/example_pingpong.yaml --no-persist --max-iterations 2
```

Expected shape:

```text
# Experiment: pingpong baseline

- State: completed
- Proposer: grid
- Iterations: 2/2
- Best iteration: #1, pass@1 = 1

Top failure modes:
- missing_required_substring
```

Want to see every grader and funnel match kind in one place? Copy the
kitchen-sink example — also offline, no key:

```bash
selfevals examples copy showcase
selfevals run evals/experiments/example_showcase.yaml --no-persist
```

To persist runs and inspect them later:

```bash
selfevals --db ./selfevals.sqlite run evals/experiments/example_pingpong.yaml
selfevals --db ./selfevals.sqlite experiment list <workspace_id>
selfevals --db ./selfevals.sqlite report <workspace_id> <experiment_id>
selfevals --db ./selfevals.sqlite report <workspace_id> <experiment_id> --format json
```

`--db` is global. Put it before the subcommand.

## Copy/Paste For Your Agent

Paste this into Claude Code, Codex, Cursor, or any repo-aware agent. It makes
the agent the eval lead and keeps the human as the product/error-analysis gate.

```text
You are the self-improving eval lead for this project.

Goal:
Set up selfevals so this repo can measure and improve its agent behavior with a
real eval loop, not vibes. You own the setup, first dataset, first experiment,
first report, and the next recommended iteration. The human is the final judge
for product intent, failure taxonomy, and whether a proposed change should ship.

Rules:
- Do not call model providers from selfevals directly. Wire selfevals to the
  project agent through an embedded, CLI, or HTTP adapter.
- Start with 5-20 high-signal eval cases from real product behavior or the
  closest available fixtures. Prefer golden/regression cases over toy cases.
- Use deterministic graders wherever the expected behavior is objective.
- Use set_match for extraction, classification, intent, entity, or multi-label work.
- Use judge_panel only for genuinely open-ended quality calls.
- Persist runs to ./selfevals.sqlite.
- Keep the YAML, datasets, and adapter small enough that a human can review them.
- After each run, explain what failed, what changed, and what you recommend next.
- Never auto-ship product changes. Propose them, show evidence, and ask the human.

Tasks:
1. Install selfevals or add it to this repo's dev environment.
2. Find the agent entrypoint. If there is no clean entrypoint, create the thinnest
   adapter wrapper possible.
3. Create evals/datasets/<first_eval>.jsonl with real cases.
4. Create evals/experiments/<first_eval>.yaml.
5. Run:
   selfevals --db ./selfevals.sqlite run evals/experiments/<first_eval>.yaml
6. Render:
   selfevals --db ./selfevals.sqlite report <workspace_id> <experiment_id>
   selfevals --db ./selfevals.sqlite report <workspace_id> <experiment_id> --format json
7. If cases fail, run:
   selfevals analyze pull <workspace_id> <experiment_id> > selfevals-analysis.json
   Then classify failures, propose candidate failure modes, and show the human
   what should be promoted.
8. Propose the next iteration:
   - prompt/model parameter change
   - retrieval/tooling fix
   - new grader
   - new cases
   - or "do not change code yet, dataset is too weak"

Definition of done:
- A human can run one command and get a report.
- The report names the best iteration and top failure modes.
- The repo has a repeatable eval harness, not a one-off script.
- The next improvement is backed by measured failures.
```

The intended operating model is simple: agents run the loop, humans judge the
meaning. Let the agent do the plumbing, run the sweeps, read the traces, and
bring a recommendation. The human decides whether the eval cases are true, the
failure taxonomy is fair, and the proposed change matches the product.

## The Contract

An experiment is a YAML file with four things:

- `experiment`: what you are optimizing and what counts as success.
- `dataset`: eval cases, inline or JSONL.
- `agent`: embedded, CLI, or HTTP adapter.
- `graders`: optional explicit grader config.

Small example:

```yaml
workspace: ws_01HZZZZZZZZZZZZZZZZZZZZZZZ

experiment:
  name: support reply temperature sweep
  goal: choose the safest temperature for support answers
  mode: handoff
  taxonomy:
    target_features: [support.reply_quality]
  datasets:
    optimization: { id: ds_support, version: 1 }
  target:
    primary: { name: pass@1, operator: ">=", value: 0.90 }
  editable:
    model_params: true
  frozen:
    fleet: { id: flt_prod }
    agents: [{ id: ag_support }]
    datasets: [{ id: ds_support }]
  proposer:
    strategy: grid
  search_space:
    model_params:
      temperature: [0.0, 0.3, 0.7]
  run:
    sandbox: live_sandboxed
    max_iterations: 3

dataset:
  cases_path: ../datasets/support.jsonl

agent:
  type: http
  url: http://localhost:9000/run
  timeout_seconds: 30

graders:
  - name: policy
    type: deterministic
  - name: quality
    type: judge_panel
    params:
      rubric: "Answer the user, cite policy, do not invent facts."
      n_judges: 3
      consensus: majority
```

The HTTP adapter receives a JSON `AdapterRequest` and must return an
`AdapterResponse`: content, optional structured output, tool uses, tokens,
cost, and provider metadata.

See [docs/eval_config.md](docs/eval_config.md) and [docs/adapters.md](docs/adapters.md).

## Built-In Graders

| Grader                  | Use it for                                                                                                                                         |
| ----------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `deterministic`         | Required substrings, forbidden substrings, tool checks, regex, exact structured output.                                                            |
| `set_match`             | Multi-label detection and extraction. Scores completeness, precision, recall, and F1.                                                              |
| `funnel`                | Declarative N-level scoring (finder→resolver→…). Per-level matches, gating short-circuit, and a drill-down breakdown with per-level failure modes. |
| `judge_panel`           | Rubric grading with multiple judges and majority/unanimous/weighted consensus.                                                                     |
| `llm_judge`             | Single rubric-driven judge.                                                                                                                        |
| `trajectory`            | Tool-call and decision-sequence checks.                                                                                                            |
| `artifact_completeness` | Required sections in generated artifacts.                                                                                                          |
| `guardrail`             | Pass/fail guardrail checks.                                                                                                                        |

Custom graders can be registered in code or referenced by dotted path:

```yaml
graders:
  - name: task_shape
    type: my_pkg.graders:TaskShapeGrader
```

## Output

Every run produces an optimization report:

- best iteration
- proposed parameters
- primary metric and deltas
- guardrail and reliability metrics
- per-grader pass rates
- funnel breakdowns
- failure mode counts
- cost/time summary when present
- decision outcome: keep, reject, investigate, spawn sub-experiment, or require tradeoff review

JSON output is stable and documented in
[docs/json_report_schema.md](docs/json_report_schema.md).

```bash
selfevals run evals/experiments/my_eval.yaml --format json --no-persist
```

## API And Dashboard

Run the API:

```bash
python -m selfevals.api --host 127.0.0.1 --port 8000 --db ./selfevals.sqlite
```

Or through the CLI:

```bash
selfevals --db ./selfevals.sqlite serve --host 127.0.0.1 --port 8000 --no-web
```

Launch an experiment over HTTP:

```bash
curl -s -X POST "http://localhost:8000/api/workspaces/<ws>/experiments/run" \
  -H "content-type: application/json" \
  -d '{"spec_path":"evals/experiments/example_pingpong.yaml","max_iterations":2}'
```

The API returns `202` and runs the optimization loop in the background. Poll the
experiment endpoint or stream spans from the dashboard.

Full API reference: [docs/api_reference.md](docs/api_reference.md).

## Error Analysis Loop

`selfevals` can turn failed traces into a working taxonomy.

```bash
selfevals analyze pull <workspace_id> <experiment_id> > bundle.json
# external agent or human labels failures, proposes modes, writes result.json
selfevals analyze push <workspace_id> <experiment_id> < result.json
selfevals failuremode list <workspace_id> --status candidate
selfevals failuremode promote <workspace_id> <failure_mode_id>
```

This is deliberate: `selfevals` measures and stores the evidence. Your agent or
team does the analysis. Humans gate the taxonomy.

## CLI Surface

```text
selfevals init <slug>
selfevals run <spec.yaml>
selfevals report <workspace_id> <experiment_id> [--format markdown|json]
selfevals compare <workspace_id> <iteration_a> <iteration_b>
selfevals estimate --cases N --space-size N --reps N --cost-per-call USD
selfevals workspace show <workspace_id>
selfevals experiment list|show <workspace_id> [experiment_id]
selfevals iteration list <workspace_id> <experiment_id>
selfevals dataset create|import|list|show|freeze ...
selfevals analyze pull|push <workspace_id> <experiment_id>
selfevals failuremode list|promote|retire|merge|edit ...
selfevals skills list|path <name>
selfevals examples copy pingpong
selfevals serve
```

## Project Layout

```text
src/selfevals/
  schemas/        Pydantic contracts for cases, experiments, traces, metrics.
  runner/         Agent adapters, executor, sandbox modes, launch wiring.
  graders/        Built-in graders and registry.
  optimization/   Proposers, aggregation, convergence, best-iteration selection.
  decision/       Decision matrix.
  trace/          Native recorder and OpenTelemetry import path.
  storage/        SQLite persistence and filesystem object store.
  reporter/       Markdown and JSON reports.
  analysis/       Error-analysis pull/push handshake.
  api/            FastAPI bridge and live span streaming.
  cli/            `selfevals` command.
web/              Svelte dashboard.
docs/             Specs, API reference, adapter guide, report schema.
tests/            Pytest suite mirroring package layout.
```

## Development

```bash
uv sync --all-extras --dev
uv run pytest
uv run ruff check .
uv run mypy src/selfevals
```

Package smoke:

```bash
uv run selfevals --version
uv run selfevals run src/selfevals/examples/evals/experiments/example_pingpong.yaml --no-persist --max-iterations 2
```

## Status

Alpha, but real.

The CLI run path, adapters, graders, SQLite persistence, reports, API run
endpoint, live span bridge, trace detail endpoints, set matching, and judge
panel are implemented and tested.

Known rough edges:

- The dashboard is still evolving.
- HTTP adapters do not expose retry/backoff policy yet.
- Advanced proposers such as Bayesian, bandit, and evolutionary search are not implemented.

See [CHANGELOG.md](CHANGELOG.md) for release history.

## License

Apache-2.0
