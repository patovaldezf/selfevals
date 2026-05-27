# Contributing

## Development setup

```bash
uv sync --all-extras --dev        # venv + every provider extra + dev tooling
```

The four checks CI runs (`.github/workflows/ci.yml`):

```bash
uv run ruff check .               # lint
uv run mypy src/selfevals         # types, --strict
uv run pytest                     # tests
uv run python -m build && uv run twine check dist/*   # packaging
```

## Running tests

The default test surface needs no extras. A handful of tests exercise the
optional telemetry and web API surfaces and are skipped (or fail) without
them:

```bash
# Default surface (no extras required):
uv run pytest --ignore=tests/api --ignore=tests/sdk \
  --ignore=tests/runner/test_otlp_receiver.py

# Full surface — install the extras the tests import:
uv sync --extra telemetry --extra web --dev
uv run pytest
```

Run a single file or test:

```bash
uv run pytest tests/graders/test_deterministic.py
uv run pytest -k "structured_output"
```

`tests/` mirrors `src/selfevals/` package-for-package, so the home for a new
test is the path matching the module under test.

## Where things live

- **Add a grader** → `src/selfevals/graders/`, register it in
  `graders/registry.py`, mirror a test under `tests/graders/`.
- **Add an adapter** → `src/selfevals/runner/adapters.py` (keep the
  `(AdapterRequest) -> AdapterResponse` contract); document it in
  `docs/adapters.md`.
- **Add a proposer** → `src/selfevals/optimization/proposers.py`.
- **A new CLI command** → wire the parser in `cli/main.py`, the handler in
  `cli/commands.py`, and update the CLI table in `README.md`.

## Web UI

The SvelteKit app lives in `web/` with its own toolchain:

```bash
cd web && npm ci && npm run check && npm run build
```

## Pull requests

Keep changes scoped. Add tests for behavior changes, run the four CI checks
locally, and update docs (`README.md`, `docs/STATUS.md`, `CHANGELOG.md`)
when user-facing commands, the CLI surface, or package metadata change.
`docs/STATUS.md` is the honest what-works/what-doesn't snapshot — keep it
truthful per release.
