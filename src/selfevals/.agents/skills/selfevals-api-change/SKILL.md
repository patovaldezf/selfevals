---
name: selfevals-api-change
description: API-change workflow for selfevals FastAPI routes, schemas, auth, workspace authorization, OpenAPI contracts, run jobs, metrics, datasets, traces, and frontend API consumers.
---

# selfevals API Change

## Workflow

1. Start from `src/selfevals/api/app.py`, `api/schemas.py`, and the relevant helper module under `api/`.
2. Keep handlers thin. New route groups belong in routers or focused helper modules, not in a larger `build_app()` body.
3. Use dependency-managed storage; do not open storage directly inside handlers unless a test-only seam requires it.
4. Enforce workspace isolation and role checks for mutations. Local header auth is only a development mode, not a shared-deploy security boundary.
5. Add or update API tests for status code, response shape, authorization, and cross-workspace denial.
6. If response shapes change, update OpenAPI-derived frontend types or document why the shape is private.

## Error Policy

- Catch `EntityNotFoundError` and expected operation errors explicitly.
- Let validation, mapper, database, and corruption errors surface as 500s with logs.
- Do not return `[]`, `None`, or 404 from a broad `except Exception`.
