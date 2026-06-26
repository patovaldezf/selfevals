"""Mapper registry lookup helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from selfevals.storage.postgres.mappers.base import EntityMapper, _registry

if TYPE_CHECKING:
    from selfevals.schemas._base import BaseEntity

#: Read-only view of the registered mappers, keyed by entity class name.
MAPPERS = _registry()


def mapper_for(entity_cls: type[BaseEntity]) -> EntityMapper[Any]:
    """Return the mapper for an entity class, or raise if none is registered."""
    return mapper_for_name(entity_cls.__name__)


def mapper_for_name(name: str) -> EntityMapper[Any]:
    mapper = _registry().get(name)
    if mapper is None:
        raise NotImplementedError(
            f"no Postgres mapper registered for entity type {name!r}; "
            "add one under selfevals.storage.postgres.mappers"
        )
    return mapper
