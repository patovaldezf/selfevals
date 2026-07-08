"""Shared per-app dependencies passed to route registration modules.

`build_app()` constructs one `AppDeps` and hands it to each
`api.routes.*.register()` function, so route modules never reach into global
state or reconstruct storage/object-store wiring themselves.
"""

from __future__ import annotations

from collections.abc import Iterator

from selfevals.storage.filesystem import FilesystemObjectStore
from selfevals.storage.interface import StorageInterface


class AppDeps:
    """Storage/object-store wiring shared across all route modules."""

    def __init__(self, *, storage_url: str, object_store: FilesystemObjectStore) -> None:
        self.storage_url = storage_url
        self.object_store = object_store

    def storage(self) -> Iterator[StorageInterface]:
        """FastAPI `yield` dependency: closes the connection after the response."""
        from selfevals.storage.factory import open_storage

        store = open_storage(self.storage_url)
        try:
            yield store
        finally:
            store.close()

    def storage_factory(self) -> StorageInterface:
        from selfevals.storage.factory import open_storage

        return open_storage(self.storage_url)
