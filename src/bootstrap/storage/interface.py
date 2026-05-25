"""Abstract storage and object-store contracts + WorkspaceScope.

The two interfaces here are the only API the rest of bootstrap uses to
persist data. A future Postgres/S3 backend implements them the same way
the MVP SQLite/filesystem backend does, and nothing else changes.

Workspace isolation is structural: you obtain a `WorkspaceScope` for a
given workspace_id; all reads and writes go through it. Direct access to
the underlying connection is not part of the public API.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Self

from bootstrap.storage.errors import WorkspaceMismatchError

if TYPE_CHECKING:
    from types import TracebackType

    from bootstrap.schemas._base import BaseEntity


@dataclass(frozen=True)
class ListFilter:
    """Filter spec for `list_entities`.

    Filters are AND-ed. Each value is matched with `=` against the stored
    column (or row-as-dict for the SQLite generic table). For status-style
    enums pass `.value` strings, not enum instances.
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

    # --- single-entity CRUD ---

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

    # --- helpers ---

    def assert_owns(self, entity: BaseEntity) -> None:
        """Guard: raise if `entity.workspace_id` doesn't match the scope."""
        if entity.workspace_id != self.workspace_id:
            raise WorkspaceMismatchError(self.workspace_id, entity.workspace_id)
