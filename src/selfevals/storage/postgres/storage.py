"""Postgres implementation of StorageInterface (relational-canonical).

Every entity type is persisted to its own typed table(s) via an
:class:`~selfevals.storage.postgres.mappers.base.EntityMapper`. There is no
generic ``entities`` table and no JSON catch-all for the entity body — the
relational rows are the source of truth. Only genuinely schemaless fields are
JSONB columns inside those tables.

Optimistic concurrency is enforced with an atomic compare-and-swap
(``UPDATE ... WHERE id = %s AND version = %s``) plus a rowcount check, so two
concurrent writers cannot both win. Multi-entity writes can be wrapped in
:meth:`PostgresStorage.transaction` for all-or-nothing semantics.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Self

from selfevals.storage.errors import (
    EntityNotFoundError,
    OptimisticConcurrencyError,
    WorkspaceMismatchError,
)
from selfevals.storage.interface import ListFilter, StorageInterface, WorkspaceScope
from selfevals.storage.postgres import metrics as _metrics
from selfevals.storage.postgres import queries as _queries
from selfevals.storage.postgres.mappers import mapper_for
from selfevals.storage.postgres.migrations import apply_migrations

if TYPE_CHECKING:
    from selfevals.api.schemas import WorkspaceSummary
    from selfevals.schemas._base import BaseEntity
    from selfevals.schemas.eval_case import EvalCase
    from selfevals.schemas.experiment import Experiment
    from selfevals.schemas.trace import Trace


class PostgresStorage(StorageInterface):
    """Postgres-backed storage with a fully-normalized relational schema."""

    def __init__(self, dsn: str) -> None:
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - psycopg is a core dep
            raise RuntimeError(
                "PostgresStorage requires psycopg (a core dependency); "
                "reinstall selfevals."
            ) from exc
        self._dsn = dsn
        # autocommit by default: each statement commits on its own. The
        # transaction() context manager flips this off for atomic batches.
        self._conn: Any = psycopg.connect(dsn, autocommit=True)
        apply_migrations(self._conn)

    def open(self, workspace_id: str) -> WorkspaceScope:
        if not workspace_id:
            raise ValueError("workspace_id must be a non-empty string")
        return _PostgresScope(self._conn, workspace_id)

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Run a block of writes atomically.

        Flips off autocommit for the duration so all writes in the block commit
        together or roll back together. Nested use is not supported.
        """
        if not self._conn.autocommit:
            raise RuntimeError("transaction() is not re-entrant")
        self._conn.autocommit = False
        try:
            yield
        except BaseException:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()
        finally:
            self._conn.autocommit = True

    # -- hot query helpers (part of the storage contract) -------------------

    def list_workspace_summaries(self) -> list[WorkspaceSummary]:
        return _queries.list_workspace_summaries(self._conn)

    def workspace_by_slug_owner(self, *, slug: str, user_id: str) -> Any | None:
        return _queries.workspace_by_slug_owner(self._conn, slug=slug, user_id=user_id)

    def list_experiments_page(
        self,
        *,
        workspace_id: str,
        limit: int,
        offset: int,
        state: str | None,
        feature: str | None,
    ) -> tuple[list[Experiment], int, dict[str, int]]:
        return _queries.list_experiments_page(
            self._conn,
            workspace_id=workspace_id,
            limit=limit,
            offset=offset,
            state=state,
            feature=feature,
        )

    def eval_cases_for_experiment(self, workspace_id: str, experiment_id: str) -> list[EvalCase]:
        return _queries.eval_cases_for_experiment(self._conn, workspace_id, experiment_id)

    def latest_trace_refs_by_case(
        self, workspace_id: str, experiment_id: str
    ) -> dict[str, tuple[str, str]]:
        return _queries.latest_trace_refs_by_case(self._conn, workspace_id, experiment_id)

    def traces_for_experiment_iteration(
        self, workspace_id: str, experiment_id: str, iteration: int
    ) -> list[Trace]:
        return _queries.traces_for_experiment_iteration(
            self._conn, workspace_id, experiment_id, iteration
        )

    def trace_by_id_or_run_id(self, workspace_id: str, trace_id: str) -> Trace | None:
        return _queries.trace_by_id_or_run_id(self._conn, workspace_id, trace_id)

    def traces_by_thread_id(self, workspace_id: str, thread_id: str) -> list[Trace]:
        return _queries.traces_by_thread_id(self._conn, workspace_id, thread_id)

    # -- metrics rollups ----------------------------------------------------

    def pass_rate_metrics(self, **kwargs: Any) -> list[dict[str, Any]]:
        return _metrics.pass_rate_metrics(self._conn, **kwargs)

    def failure_mode_metrics(self, **kwargs: Any) -> list[dict[str, Any]]:
        return _metrics.failure_mode_metrics(self._conn, **kwargs)

    def tool_metrics(self, **kwargs: Any) -> list[dict[str, Any]]:
        return _metrics.tool_metrics(self._conn, **kwargs)

    def cost_metrics(self, **kwargs: Any) -> list[dict[str, Any]]:
        return _metrics.cost_metrics(self._conn, **kwargs)

    def token_metrics(self, **kwargs: Any) -> list[dict[str, Any]]:
        return _metrics.token_metrics(self._conn, **kwargs)

    def latency_metrics(self, **kwargs: Any) -> list[dict[str, Any]]:
        return _metrics.latency_metrics(self._conn, **kwargs)


class _PostgresScope(WorkspaceScope):
    def __init__(self, conn: Any, workspace_id: str) -> None:
        self._conn = conn
        self.workspace_id = workspace_id
        self._closed = False

    def close(self) -> None:
        self._closed = True

    def __enter__(self) -> Self:
        if self._closed:
            raise RuntimeError("scope has been closed")
        return self

    def put_entity(self, entity: BaseEntity) -> None:
        self._guard_open()
        self.assert_owns(entity)
        mapper = mapper_for(type(entity))
        type_tag = type(entity).__name__
        with self._conn.cursor() as cur:
            cur.execute(
                f"SELECT version, workspace_id FROM {mapper.table} WHERE id = %s",
                (entity.id,),
            )
            existing = cur.fetchone()
            if existing is None:
                mapper.upsert(cur, entity)
                return
            stored_version = int(existing[0])
            stored_ws = existing[1]
            if stored_ws != self.workspace_id:
                raise WorkspaceMismatchError(self.workspace_id, stored_ws)
            # Atomic CAS: allow same-version idempotent re-writes and
            # forward-by-one bumps; anything else is a concurrency violation.
            if stored_version not in (entity.version - 1, entity.version):
                raise OptimisticConcurrencyError(
                    entity_type=type_tag,
                    entity_id=entity.id,
                    expected=entity.version,
                    found=stored_version,
                )
            mapper.upsert(cur, entity)

    def get_entity(self, entity_type: type[BaseEntity], entity_id: str) -> BaseEntity:
        self._guard_open()
        mapper = mapper_for(entity_type)
        type_tag = entity_type.__name__
        with self._conn.cursor() as cur:
            loaded: BaseEntity | None = mapper.load(cur, self.workspace_id, entity_id)
            if loaded is not None:
                return loaded
            # Differentiate "wrong workspace" from "missing" for clearer audit.
            cur.execute(
                f"SELECT workspace_id FROM {mapper.table} WHERE id = %s",
                (entity_id,),
            )
            cross = cur.fetchone()
        if cross is not None:
            raise WorkspaceMismatchError(self.workspace_id, cross[0])
        raise EntityNotFoundError(type_tag, entity_id, self.workspace_id)

    def list_entities(
        self,
        entity_type: type[BaseEntity],
        filter_: ListFilter | None = None,
    ) -> list[BaseEntity]:
        self._guard_open()
        mapper = mapper_for(entity_type)
        filter_ = filter_ or ListFilter()
        with self._conn.cursor() as cur:
            return mapper.load_many(
                cur,
                workspace_id=self.workspace_id,
                where=filter_.where,
                order_by=filter_.order_by,
                order_desc=filter_.order_desc,
                limit=filter_.limit,
                offset=filter_.offset,
            )

    def delete_entity(self, entity_type: type[BaseEntity], entity_id: str) -> None:
        self._guard_open()
        mapper = mapper_for(entity_type)
        type_tag = entity_type.__name__
        with self._conn.cursor() as cur:
            cur.execute(
                f"SELECT workspace_id FROM {mapper.table} WHERE id = %s",
                (entity_id,),
            )
            owner = cur.fetchone()
            if owner is None:
                raise EntityNotFoundError(type_tag, entity_id, self.workspace_id)
            if owner[0] != self.workspace_id:
                raise WorkspaceMismatchError(self.workspace_id, owner[0])
            mapper.delete(cur, entity_id)

    def exists(self, entity_type: type[BaseEntity], entity_id: str) -> bool:
        self._guard_open()
        mapper = mapper_for(entity_type)
        with self._conn.cursor() as cur:
            cur.execute(
                f"SELECT 1 FROM {mapper.table} "
                "WHERE id = %s AND workspace_id = %s",
                (entity_id, self.workspace_id),
            )
            return cur.fetchone() is not None

    def _guard_open(self) -> None:
        if self._closed:
            raise RuntimeError("scope has been closed; open a new one")
