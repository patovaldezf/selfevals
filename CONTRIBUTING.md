# Contributing

## Development

```bash
uv sync --all-extras --dev
uv run ruff check .
uv run mypy src/selfevals
uv run pytest
cd web
npm ci
npm run check
npm run build
```

## Pull requests

Keep changes scoped, add tests for behavior changes, and update docs when user-facing
commands or package metadata change.
