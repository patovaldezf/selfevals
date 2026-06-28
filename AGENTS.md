# selfevals Agent Rules

These rules apply to coding agents working in this repository.

## Before Editing

- Read `CLAUDE.md`, `docs/STATUS.md`, and `docs/TECHNICAL_DEBT.md` for non-trivial changes.
- Verify docs against source before acting; some debt records are historical and already resolved.
- Preserve unrelated local changes.

## Architecture Rules

- Postgres is the canonical storage backend. Use forward-only migrations and typed mappers.
- Do not reintroduce generic JSON entity persistence or ad hoc storage capability checks.
- API handlers must stay thin and use dependency-managed storage cleanup.
- Frontend code must use the shared API client/request layer; do not add direct internal `fetch` calls or hardcoded auth headers in components.
- Catch expected domain errors explicitly. Do not turn corruption, validation, or storage failures into empty states.

## Required Checks

Run the smallest relevant set during development, and before handoff run:

```bash
uv run ruff check .
uv run mypy src/selfevals
uv run python scripts/audit_technical_debt.py --fail-on-regression
```

For web changes:

```bash
cd web && npm run lint && npm run check && npm run build
```

For landing changes:

```bash
cd landing && npm run lint && npm run build
```

Only update `docs/quality/technical_debt_baseline.json` after human review.
