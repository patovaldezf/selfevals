"""Storage backend selection.

SQLite remains the default local backend. Hosted/high-volume deployments can
opt into Postgres with ``SELFEVALS_STORAGE_URL=postgresql://...``.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote, urlparse

from selfevals.storage.interface import StorageInterface
from selfevals.storage.sqlite import SQLiteStorage

DEFAULT_SQLITE_PATH = "./selfevals.sqlite"
STORAGE_URL_ENV = "SELFEVALS_STORAGE_URL"
SQLITE_DB_ENV = "SELFEVALS_DB"


def resolve_storage_url(value: str | None = None) -> str:
    """Resolve configured storage, preserving the historical SQLite default."""
    if value:
        return value
    env_url = os.environ.get(STORAGE_URL_ENV)
    if env_url:
        return env_url
    return os.environ.get(SQLITE_DB_ENV, DEFAULT_SQLITE_PATH)


def storage_url_label(url: str) -> str:
    """Human-readable storage label for health/debug responses."""
    if _is_postgres_url(url):
        parsed = urlparse(url)
        host = parsed.hostname or "localhost"
        db = (parsed.path or "").lstrip("/") or "postgres"
        return f"postgresql://{host}/{db}"
    path = sqlite_path_from_url(url)
    return path


def open_storage(url: str | None = None) -> StorageInterface:
    resolved = resolve_storage_url(url)
    if _is_postgres_url(resolved):
        from selfevals.storage.postgres import PostgresStorage

        return PostgresStorage(resolved)
    path = sqlite_path_from_url(resolved)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return SQLiteStorage(path)


def sqlite_path_from_url(url: str) -> str:
    if url == ":memory:":
        return url
    parsed = urlparse(url)
    if parsed.scheme in {"", None}:
        return url
    if parsed.scheme != "sqlite":
        raise ValueError(f"unsupported storage URL scheme: {parsed.scheme!r}")
    if parsed.netloc and parsed.netloc not in {"localhost"}:
        raise ValueError("sqlite storage URLs must be local paths")
    path = unquote(parsed.path)
    if path.startswith("//"):
        path = path[1:]
    if not path:
        raise ValueError("sqlite storage URL must include a path")
    return path


def is_sqlite_storage_url(url: str) -> bool:
    return not _is_postgres_url(url)


def object_store_base_for_storage_url(url: str) -> Path:
    """Return a local object-store base for the current v1 implementation.

    Postgres stores rows, but payload offload still uses the filesystem object
    store in this phase. Operators can place it with ``SELFEVALS_OBJECTS_DIR``.
    """
    override = os.environ.get("SELFEVALS_OBJECTS_DIR")
    if override:
        return Path(override)
    if _is_postgres_url(url):
        return Path("./objects")
    return Path(sqlite_path_from_url(url)).parent / "objects"


def _is_postgres_url(url: str) -> bool:
    return url.startswith(("postgresql://", "postgres://"))
