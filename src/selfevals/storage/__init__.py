"""Storage layer: persistence + object store + workspace isolation.

The storage interfaces are abstract on purpose. MVP ships SQLite +
filesystem object store; a future PR can drop in Postgres + S3 without
touching application code.

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
    DEFAULT_SQLITE_PATH,
    SQLITE_DB_ENV,
    STORAGE_URL_ENV,
    is_sqlite_storage_url,
    object_store_base_for_storage_url,
    open_storage,
    resolve_storage_url,
    sqlite_path_from_url,
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
from selfevals.storage.sqlite import SQLiteStorage

__all__ = [
    "DEFAULT_SQLITE_PATH",
    "SQLITE_DB_ENV",
    "STORAGE_URL_ENV",
    "EntityNotFoundError",
    "FilesystemObjectStore",
    "IntegrityViolationError",
    "ListFilter",
    "ObjectNotFoundError",
    "ObjectStoreInterface",
    "OptimisticConcurrencyError",
    "PointerHashMismatchError",
    "SQLiteStorage",
    "SeededWorkspace",
    "StorageError",
    "StorageInterface",
    "WorkspaceMismatchError",
    "WorkspaceScope",
    "is_sqlite_storage_url",
    "make_pointer",
    "object_store_base_for_storage_url",
    "open_storage",
    "parse_pointer",
    "resolve_storage_url",
    "seed_workspace",
    "sqlite_path_from_url",
    "storage_url_label",
]
