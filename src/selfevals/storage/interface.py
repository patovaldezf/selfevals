"""Abstract storage and object-store contracts + WorkspaceScope.

The two interfaces here are the only API the rest of selfevals uses to
persist data. The Postgres/filesystem backend implements them; a future
backend (e.g. S3 for objects) would implement the same contracts and
nothing else changes.

Workspace isolation is structural: you obtain a `WorkspaceScope` for a
given workspace_id; all reads and writes go through it. Direct access to
the underlying connection is not part of the public API.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Self

from selfevals.storage.errors import WorkspaceMismatchError

if TYPE_CHECKING:
    from collections.abc import Iterator
    from datetime import datetime
    from types import TracebackType

    from selfevals.api.schemas import WorkspaceSummary
    from selfevals.schemas._base import BaseEntity
    from selfevals.schemas.eval_case import EvalCase
    from selfevals.schemas.experiment import Experiment
    from selfevals.schemas.job import ScenarioJob
    from selfevals.schemas.trace import Trace


@dataclass(frozen=True)
class ListFilter:
    """Filter spec for `list_entities`.

    Filters are AND-ed. Each value is matched with `=` against the stored
    column. For status-style enums pass `.value` strings, not enum instances.
    """

    where: dict[str, Any] = field(default_factory=dict)
    limit: int | None = None
    offset: int = 0
    order_by: str = "created_at"
    order_desc: bool = True


class StorageInterface(ABC):
    """Persistence for Pydantic entities, always scoped to a workspace."""

    @abstractmethod
    def open(self, workspace_id: str) -> WorkspaceScope:
        """Return a `WorkspaceScope` bound to `workspace_id`."""

    @abstractmethod
    def close(self) -> None:
        """Release underlying resources (file handles, connections)."""

    # -- atomic batches -----------------------------------------------------

    @contextmanager
    @abstractmethod
    def transaction(self) -> Iterator[None]:
        """Run a block of writes atomically (all-or-nothing). Not re-entrant."""
        raise NotImplementedError
        yield  # pragma: no cover - signals a generator/contextmanager

    # -- hot query helpers (part of the storage contract) -------------------

    @abstractmethod
    def list_workspace_summaries(self) -> list[WorkspaceSummary]:
        """Cross-workspace listing for the dashboard index."""

    @abstractmethod
    def workspace_by_slug_owner(self, *, slug: str, user_id: str) -> Any | None:
        """Resolve a workspace by (slug, owner) or None."""

    @abstractmethod
    def list_experiments_page(
        self,
        *,
        workspace_id: str,
        limit: int,
        offset: int,
        state: str | None,
        feature: str | None,
    ) -> tuple[list[Experiment], int, dict[str, int]]:
        """Paginated experiments + total + per-experiment iteration counts."""

    @abstractmethod
    def eval_cases_for_experiment(
        self, workspace_id: str, experiment_id: str
    ) -> list[EvalCase]:
        """All EvalCases persisted under an experiment."""

    @abstractmethod
    def latest_trace_refs_by_case(
        self, workspace_id: str, experiment_id: str
    ) -> dict[str, tuple[str, str]]:
        """Map eval_case_id -> (run_id, trace_id) for the latest trace per case."""

    @abstractmethod
    def traces_for_experiment_iteration(
        self, workspace_id: str, experiment_id: str, iteration: int
    ) -> list[Trace]:
        """All traces for one experiment iteration."""

    @abstractmethod
    def trace_by_id_or_run_id(self, workspace_id: str, trace_id: str) -> Trace | None:
        """Look up a Trace by entity id or run_id; None if missing."""

    @abstractmethod
    def traces_by_thread_id(self, workspace_id: str, thread_id: str) -> list[Trace]:
        """All traces sharing a thread_id (a conversation)."""

    # -- run-job durability (sweeper + heartbeat) ---------------------------

    @abstractmethod
    def expired_run_job_leases(
        self, *, now: datetime, limit: int = 100
    ) -> list[tuple[str, str]]:
        """Cross-workspace ``(workspace_id, job_id)`` of run jobs with a lapsed lease."""

    @abstractmethod
    def touch_run_job_lease(
        self, *, workspace_id: str, job_id: str, owner: str, lease_expires_at: datetime
    ) -> bool:
        """Renew a job's lease via a direct unversioned UPDATE; True if renewed."""

    # -- scenario jobs (sharded per-case claim/plan/barrier) ----------------

    @abstractmethod
    def claim_scenario_jobs(
        self,
        *,
        run_job_id: str,
        iteration: int,
        worker_id: str,
        lease_until: datetime,
        batch: int,
    ) -> list[ScenarioJob]:
        """Atomically claim up to ``batch`` pending scenario jobs (SKIP LOCKED)."""

    @abstractmethod
    def insert_scenario_jobs(self, jobs: list[ScenarioJob]) -> int:
        """Batch-insert scenario jobs, idempotent on (run_job_id, iteration, case_id)."""

    @abstractmethod
    def barrier_counts(self, *, run_job_id: str, iteration: int) -> dict[str, int]:
        """Count scenario jobs by status for one iteration (coordinator barrier)."""

    @abstractmethod
    def finalize_scenario_job(
        self, *, job_id: str, status: str, error: str | None, finished_at: datetime
    ) -> None:
        """Persist a scenario job's terminal/retry state via direct SQL."""

    @abstractmethod
    def touch_scenario_job_lease(
        self, *, job_id: str, worker_id: str, lease_until: datetime
    ) -> bool:
        """Heartbeat a claimed/running scenario job's lease; True if renewed."""

    @abstractmethod
    def expired_scenario_job_leases(
        self, *, now: datetime, limit: int = 100
    ) -> list[tuple[str, str]]:
        """Cross-run ``(workspace_id, scenario_job_id)`` whose worker died."""

    @abstractmethod
    def write_scenario_outcome(
        self,
        *,
        outcome_id: str,
        workspace_id: str,
        run_job_id: str,
        scenario_job_id: str,
        experiment_id: str,
        iteration: int,
        fields: dict[str, Any],
        now: datetime,
    ) -> None:
        """Upsert one scenario_outcomes row (relational CaseOutcome)."""

    @abstractmethod
    def scenario_outcomes_for_iteration(
        self, *, run_job_id: str, iteration: int
    ) -> list[dict[str, Any]]:
        """Read persisted CaseOutcome fields for one iteration, in case order."""

    # -- metrics rollups ----------------------------------------------------

    @abstractmethod
    def pass_rate_metrics(self, **kwargs: Any) -> list[dict[str, Any]]: ...

    @abstractmethod
    def failure_mode_metrics(self, **kwargs: Any) -> list[dict[str, Any]]: ...

    @abstractmethod
    def tool_metrics(self, **kwargs: Any) -> list[dict[str, Any]]: ...

    @abstractmethod
    def cost_metrics(self, **kwargs: Any) -> list[dict[str, Any]]: ...

    @abstractmethod
    def token_metrics(self, **kwargs: Any) -> list[dict[str, Any]]: ...

    @abstractmethod
    def latency_metrics(self, **kwargs: Any) -> list[dict[str, Any]]: ...


class ObjectStoreInterface(ABC):
    """Content-addressed payload store, always scoped to a workspace."""

    @abstractmethod
    def put(self, workspace_id: str, key: str, data: bytes) -> str:
        """Store `data` under `key` for `workspace_id`. Return a pointer URI.

        The pointer is opaque to callers; pass it back to `get` or `delete`.
        """

    @abstractmethod
    def get(self, pointer: str) -> bytes:
        """Resolve `pointer` to bytes. Raises `ObjectNotFoundError` if missing.

        The implementation MUST verify the stored content_hash matches the
        bytes on read; mismatches raise `PointerHashMismatchError`.
        """

    @abstractmethod
    def exists(self, pointer: str) -> bool: ...

    @abstractmethod
    def delete(self, pointer: str) -> None: ...

    @abstractmethod
    def workspace_for(self, pointer: str) -> str:
        """Return the workspace_id encoded in `pointer` (for isolation checks)."""


class WorkspaceScope(ABC):
    """A read/write handle bound to one workspace.

    Every method that touches data verifies the entity's `workspace_id`
    matches the scope's `workspace_id`. Writes that name a different
    workspace raise `WorkspaceMismatchError` before hitting the store.
    """

    workspace_id: str

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def put_entity(self, entity: BaseEntity) -> None:
        """Insert or update an entity. On update, optimistic concurrency
        on `version` is enforced."""

    @abstractmethod
    def get_entity(self, entity_type: type[BaseEntity], entity_id: str) -> BaseEntity:
        """Load one entity by id. Raises `EntityNotFoundError`."""

    @abstractmethod
    def list_entities(
        self,
        entity_type: type[BaseEntity],
        filter_: ListFilter | None = None,
    ) -> list[BaseEntity]:
        """List entities of a type within this workspace, with optional filter."""

    @abstractmethod
    def delete_entity(self, entity_type: type[BaseEntity], entity_id: str) -> None:
        """Hard delete (soft delete is done by setting `deleted_at` and calling
        put_entity)."""

    @abstractmethod
    def exists(self, entity_type: type[BaseEntity], entity_id: str) -> bool: ...

    def assert_owns(self, entity: BaseEntity) -> None:
        """Guard: raise if `entity.workspace_id` doesn't match the scope."""
        if entity.workspace_id != self.workspace_id:
            raise WorkspaceMismatchError(self.workspace_id, entity.workspace_id)
