---
name: selfevals-storage-change
description: Storage workflow for selfevals Postgres migrations, relational mappers, StorageInterface, transaction boundaries, optimistic concurrency, query helpers, metrics, and object-store changes.
---

# selfevals Storage Change

## Workflow

1. Treat Postgres as the canonical backend. Every persistent entity needs a typed mapper and forward-only migration.
2. Add schema changes under `src/selfevals/storage/postgres/migrations/`; never mutate applied migration semantics without a compatibility reason.
3. Keep all-or-nothing multi-entity writes inside `StorageInterface.transaction()`.
4. Preserve atomic optimistic concurrency: updates must compare the stored version and check rowcount.
5. Put hot query behavior on `StorageInterface` or focused query modules; do not reintroduce `getattr` capability discovery.
6. Add contract tests for mapper round trips, constraints, transaction rollback, and query results.

## Data Integrity

- Prefer FK, UNIQUE, CHECK, and NOT NULL constraints for invariants.
- Do not hide dangling references in API responses. Either report them explicitly or fail loudly with a repair path.
- Keep schemaless JSONB only for genuinely flexible payload fields.
