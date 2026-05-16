# bootstrap

Self-improving evals framework for AI agents.

Install the SDK, point it at your agent, and bootstrap reads your repo,
proposes an eval structure, runs experiments, and iterates on parameters
toward a target metric. CLI-first, multi-tenant from day one, agnostic to
the agent framework underneath.

> Status: **pre-alpha (0.0.x).** Schemas-first scaffolding. No runtime yet.
> See `docs/spec/` for the canonical and operational specs that drive design.

## Layout

```
src/bootstrap/        # the SDK package
  schemas/            # Pydantic v2 entities + contractual validators
  storage/            # SQLite + filesystem object store (interface abstracted)
  trace/              # native SDK decorators + OTel importer
  runner/             # agent adapters + executor + sandbox modes
  graders/            # deterministic + LLM-judge + calibration
  optimization/       # OptimizationLoop + proposers (manual/grid/random)
  decision/           # decision matrix → DecisionRecord
  reporter/           # markdown + JSON reports
  cli/                # Typer entrypoint
skills/               # markdown skills for Claude Code (propose/run/optimize)
docs/spec/            # canonical + operational specs (source of truth)
tests/                # pytest, mirrors src/bootstrap layout
```

## Dev

```bash
uv sync                # create venv + install deps
uv run pytest          # tests
uv run mypy src        # types
uv run ruff check .    # lint
```

## License

Apache-2.0
