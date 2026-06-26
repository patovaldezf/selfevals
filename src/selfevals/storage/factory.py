"""Storage backend selection.

Postgres is the only backend. Configure it with
``SELFEVALS_STORAGE_URL=postgresql://...`` (see ``.env.example``).
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

from selfevals.storage.interface import StorageInterface
from selfevals.storage.postgres import PostgresStorage

STORAGE_URL_ENV = "SELFEVALS_STORAGE_URL"


def resolve_storage_url(value: str | None = None) -> str:
    """Resolve the configured Postgres storage URL.

    Precedence: explicit ``value`` argument > ``SELFEVALS_STORAGE_URL`` env.
    There is no default fallback — Postgres is required.
    """
    if value:
        return value
    env_url = os.environ.get(STORAGE_URL_ENV)
    if env_url:
        return env_url
    raise RuntimeError(
        "no storage configured: set SELFEVALS_STORAGE_URL to a Postgres URL "
        "(e.g. postgresql://user:pass@host:5432/selfevals) or pass --db. "
        "See .env.example for the expected format."
    )


def storage_url_label(url: str) -> str:
    """Human-readable storage label for health/debug responses."""
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    db = (parsed.path or "").lstrip("/") or "postgres"
    return f"postgresql://{host}/{db}"


def open_storage(url: str | None = None) -> StorageInterface:
    resolved = resolve_storage_url(url)
    return PostgresStorage(resolved)


def object_store_base_for_storage_url(url: str) -> Path:
    """Return a local object-store base for the current v1 implementation.

    Postgres stores rows, but payload offload still uses the filesystem object
    store in this phase. Operators can place it with ``SELFEVALS_OBJECTS_DIR``.
    """
    return Path(os.environ.get("SELFEVALS_OBJECTS_DIR", "./objects"))
