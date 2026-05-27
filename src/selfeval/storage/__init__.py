"""Storage layer: persistence + object store + workspace isolation.

The storage interfaces are abstract on purpose. MVP ships SQLite +
filesystem object store; a future PR can drop in Postgres + S3 without
touching application code.

All operations are scoped to a `WorkspaceScope` — there is no way to
read or write without naming a workspace. This is the load-bearing
multi-tenant invariant of selfeval.
"""

from selfeval.storage.errors import (
    EntityNotFoundError,
    IntegrityViolationError,
    ObjectNotFoundError,
    OptimisticConcurrencyError,
    PointerHashMismatchError,
    StorageError,
    WorkspaceMismatchError,
)
from selfeval.storage.filesystem import (
    FilesystemObjectStore,
    make_pointer,
    parse_pointer,
)
from selfeval.storage.interface import (
    ListFilter,
    ObjectStoreInterface,
    StorageInterface,
    WorkspaceScope,
)
from selfeval.storage.seed import SeededWorkspace, seed_workspace
from selfeval.storage.sqlite import SQLiteStorage

__all__ = [
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
    "make_pointer",
    "parse_pointer",
    "seed_workspace",
]
