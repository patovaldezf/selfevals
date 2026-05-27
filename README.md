# selfevals

Self-improving evals framework for AI agents.

Install the SDK, point it at your agent, and selfevals reads your repo,
proposes an eval structure, runs experiments, and iterates on parameters
toward a target metric. CLI-first, multi-tenant from day one, agnostic to
the agent framework underneath.

> Status: **v0.2.0 — runtime functional.** CLI end-to-end works: load
> an experiment spec → run cases through an adapter → grade traces →
> persist iterations → render report. See `docs/spec/` for the
> canonical and operational specs that drive design.

## Install

```bash
pip install selfevals
selfevals examples copy pingpong
selfevals run evals/experiments/example_pingpong.yaml --no-persist
```

The distribution is `selfevals`; the import name and the CLI command are
both `selfevals` (`import selfevals`, `selfevals --help`).

## Quickstart from source

```bash
uv sync --extra web --extra telemetry
uv run selfevals run evals/experiments/example_pingpong.yaml --no-persist
```

Expected output: a markdown report showing two iterations, the best
one selected, and a top failure-modes table. End-to-end in <1 second
against the bundled `EmbeddedAdapter` echo agent.

To persist to SQLite and inspect afterwards:

```bash
uv run selfevals run evals/experiments/example_pingpong.yaml --db ./selfevals.sqlite
uv run selfevals experiment list <workspace_id>
uv run selfevals report <workspace_id> <experiment_id>
```

## Try with a real LLM agent

The `examples/hello_llm/` directory shows selfevals optimizing a real
Anthropic agent over three eval cases (sentiment classification,
structured extraction, and an open-ended customer-support reply) with
two graders combined: a `DeterministicGrader` for the rule-based cases
and an `LLMJudgeGrader` (driven by the same Anthropic backend with a
different system prompt) for the open-ended one. The `GridProposer`
sweeps `temperature ∈ {0.0, 0.5, 1.0}` and the report ranks them.

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # optional; see below
uv run selfevals run examples/hello_llm/experiment.yaml --no-persist
```

If `ANTHROPIC_API_KEY` is unset or the `anthropic` SDK is not installed,
the agent and the judge both fall back to deterministic fakes designed
to produce different grader outcomes across the temperature sweep — so
the example is fully runnable offline. Expect a markdown report with
three iterations, the coolest temperature winning the primary metric
(`pass@1 >= 0.7`), and the failure-modes table dominated by
`structured_output_mismatch` at the warmer settings.

The full setup lives in three files you can copy into your own repo:

- `examples/hello_llm/agent.py` — the agent + judge callables.
- `examples/hello_llm/cases.jsonl` — the three EvalCases.
- `examples/hello_llm/experiment.yaml` — proposer, target, graders.

## Adapters

selfevals ships three concrete `AgentAdapter` implementations so you can
point the loop at any agent:

- `EmbeddedAdapter` — a Python callable in-process. Best for quick tests.
- `CliCommandAdapter` — invokes a subprocess and reads JSON on stdout.
- `HttpEndpointAdapter` — POSTs each case to an HTTP endpoint and reads JSON.

See `src/selfevals/runner/adapters.py` for the contract and
[`docs/adapters.md`](docs/adapters.md) for usage examples, the per-adapter
YAML/code snippets, and a comparison table.

## Docs

- [Adapters](docs/adapters.md) — write agents that selfevals can call.
- [`docs/spec/`](docs/spec/) — canonical and operational specs (source
  of truth for design decisions).

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
  cli/                # argparse entrypoint
skills/               # markdown skills for Claude Code (propose/run/optimize)
docs/spec/            # canonical + operational specs (source of truth)
tests/                # pytest, mirrors src/selfevals layout
```

## Dev

```bash
uv sync --extra web --extra telemetry
uv run --extra web --extra telemetry pytest
uv run --extra web --extra telemetry mypy src/selfevals
uv run ruff check .    # lint
cd web && npm install && npm run check && npm run build
```

## License

Apache-2.0
