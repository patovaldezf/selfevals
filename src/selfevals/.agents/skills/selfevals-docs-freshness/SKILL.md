---
name: selfevals-docs-freshness
description: Documentation freshness workflow for selfevals README, STATUS, roadmap, frontend docs, API reference, eval config docs, changelog, and technical-debt records after behavior or tooling changes.
---

# selfevals Docs Freshness

## Workflow

1. Verify docs against code before editing. Prefer source files, tests, package manifests, and CI config over older roadmap text.
2. Keep `docs/STATUS.md` as the current-state source of truth.
3. Treat roadmap docs as intent, not implementation inventory.
4. When public behavior changes, update the nearest user-facing docs and the changelog if the change is release-worthy.
5. Remove hardcoded stale version references when possible; otherwise sync them to `pyproject.toml`.
6. If a technical-debt item is fixed, move it to a resolved section with the PR/change summary and add a guardrail if repeatable.

## Required Checks

- Search for stale terms related to the changed feature.
- Run `uv run python scripts/audit_technical_debt.py --fail-on-regression`.
- Ensure setup docs reference committed examples such as `.env.example`, never private `.env` state.
