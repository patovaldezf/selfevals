"""Per-entity relational mappers.

Each persistent entity type has an :class:`EntityMapper` that knows how to
decompose its Pydantic model into typed rows (main table + child tables) and
reassemble rows back into the model — with no generic catch-all ``entities``
table. Genuinely schemaless fields (free-form ``dict[str, Any]`` config spaces,
provider metadata, JSON schemas) are stored as JSONB columns, validated by
Pydantic at the application layer.

``MAPPERS`` is the registry the storage scope dispatches through, keyed by the
entity class name (the same tag the old generic table used).
"""

from __future__ import annotations

# Importing the entity modules registers their mappers as a side effect.
from selfevals.storage.postgres.mappers import workspace as _workspace  # noqa: F401
from selfevals.storage.postgres.mappers.base import EntityMapper, register_mapper
from selfevals.storage.postgres.mappers.registry import MAPPERS, mapper_for, mapper_for_name

__all__ = [
    "MAPPERS",
    "EntityMapper",
    "mapper_for",
    "mapper_for_name",
    "register_mapper",
]
