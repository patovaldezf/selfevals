"""Storage layer: persistence + object store + workspace isolation.

The storage interfaces are abstract on purpose. The single backend is
Postgres + filesystem object store; a future PR can drop in another backend
(e.g. S3 for objects) without touching application code.

All operations are scoped to a `WorkspaceScope` — there is no way to
read or write without naming a workspace. This is the load-bearing
multi-tenant invariant of selfevals.
"""

from selfevals.storage.errors import (
    EntityNotFoundError,
    IntegrityViolationError,
    ObjectNotFoundError,
    OptimisticConcurrencyError,
    PointerHashMismatchError,
    StorageError,
    WorkspaceMismatchError,
)
from selfevals.storage.factory import (
    STORAGE_URL_ENV,
    object_store_base_for_storage_url,
    open_storage,
    resolve_storage_url,
    storage_url_label,
)
from selfevals.storage.filesystem import (
    FilesystemObjectStore,
    make_pointer,
    parse_pointer,
)
from selfevals.storage.interface import (
    ListFilter,
    ObjectStoreInterface,
    StorageInterface,
    WorkspaceScope,
)
from selfevals.storage.seed import SeededWorkspace, seed_workspace

__all__ = [
    "STORAGE_URL_ENV",
    "EntityNotFoundError",
    "FilesystemObjectStore",
    "IntegrityViolationError",
    "ListFilter",
    "ObjectNotFoundError",
    "ObjectStoreInterface",
    "OptimisticConcurrencyError",
    "PointerHashMismatchError",
    "SeededWorkspace",
    "StorageError",
    "StorageInterface",
    "WorkspaceMismatchError",
    "WorkspaceScope",
    "make_pointer",
    "object_store_base_for_storage_url",
    "open_storage",
    "parse_pointer",
    "resolve_storage_url",
    "seed_workspace",
    "storage_url_label",
]
