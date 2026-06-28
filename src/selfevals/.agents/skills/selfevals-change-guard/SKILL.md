---
name: selfevals-change-guard
description: Guardrail workflow for any selfevals code, docs, CI, packaging, or frontend change. Use before modifying this repo, when reviewing a diff, when fixing technical debt, or when deciding which checks/docs must be updated.
---

# selfevals Change Guard

## Workflow

1. Read `CLAUDE.md`, `docs/STATUS.md`, and `docs/TECHNICAL_DEBT.md` before planning a non-trivial change.
2. Verify reported debt against code before editing; `docs/TECHNICAL_DEBT.md` contains historical items that may already be resolved.
3. Run or consider `uv run python scripts/audit_technical_debt.py --fail-on-regression` before finishing.
4. Keep new abstractions aligned with existing package boundaries. Do not create another coordination hub when a router, mapper, store, or helper module already exists.
5. Do not broaden the baseline in `docs/quality/technical_debt_baseline.json` unless a human explicitly accepts the new debt.

## Required Habits

- Preserve unrelated local changes.
- Prefer scoped refactors with focused tests over broad rewrites.
- Catch domain errors explicitly; do not convert corruption or storage failures into empty states.
- Update docs when public behavior, CLI, API responses, config, or setup changes.
- Add a guardrail when fixing a repeated mistake so future agents get blocked earlier.
