"""EntityMapper base + shared helpers for relational-canonical persistence.

A mapper owns one entity type end to end:

* ``upsert(cur, entity)`` — write the main row and any child rows.
* ``load(cur, workspace_id, entity_id)`` — read the row(s) and rebuild the model,
  or return ``None`` if absent.
* ``load_many(cur, workspace_id, where, order_by, ...)`` — list rows as models.
* ``delete(cur, entity_id)`` — remove the main row (child rows cascade via FKs).

Concrete mappers live in sibling modules (one per entity) and register
themselves via :func:`register_mapper`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from selfevals.schemas._base import BaseEntity

# Columns shared by every main entity table, in a fixed order.
SHARED_COLUMNS: tuple[str, ...] = (
    "id",
    "workspace_id",
    "version",
    "created_at",
    "updated_at",
    "deleted_at",
)

_ORDERABLE_SHARED = frozenset(SHARED_COLUMNS)


def shared_values(entity: BaseEntity) -> list[Any]:
    """Return the shared-column values for ``entity`` in ``SHARED_COLUMNS`` order."""
    return [
        entity.id,
        entity.workspace_id,
        entity.version,
        entity.created_at,
        entity.updated_at,
        entity.deleted_at,
    ]


class EntityMapper[E: BaseEntity](ABC):
    """Maps one entity type to/from its relational tables."""

    #: The Pydantic entity class this mapper handles.
    entity_cls: type[E]
    #: The main table name.
    table: str
    #: Columns on the main table that ``list_entities`` may filter/order on
    #: directly (shared columns plus mapper-specific scalar columns).
    queryable_columns: frozenset[str] = _ORDERABLE_SHARED

    @abstractmethod
    def upsert(self, cur: Any, entity: E) -> None:
        """Insert or update the entity's row(s). Caller handles version CAS."""

    @abstractmethod
    def load(self, cur: Any, workspace_id: str, entity_id: str) -> E | None:
        """Load one entity by id within a workspace, or ``None`` if absent."""

    @abstractmethod
    def load_many(
        self,
        cur: Any,
        *,
        workspace_id: str,
        where: dict[str, Any],
        order_by: str,
        order_desc: bool,
        limit: int | None,
        offset: int,
    ) -> list[E]:
        """List entities of this type within a workspace."""

    def delete(self, cur: Any, entity_id: str) -> None:
        """Delete the main row by id. Child rows cascade via FK ON DELETE CASCADE."""
        cur.execute(f"DELETE FROM {self.table} WHERE id = %s", (entity_id,))

    # -- helpers shared by concrete mappers ---------------------------------

    def _validate_order_by(self, order_by: str) -> None:
        if order_by not in self.queryable_columns:
            raise ValueError(
                f"unsupported order_by column {order_by!r} for {self.table}; "
                f"queryable: {sorted(self.queryable_columns)}"
            )

    def _scalar_where_sql(
        self, where: dict[str, Any]
    ) -> tuple[list[str], list[Any]]:
        """Translate a ListFilter ``where`` dict into SQL clauses.

        Only keys in ``queryable_columns`` are accepted; anything else is a
        programming error (the generic JSON-path fallback is gone now that every
        queryable field is a real column).
        """
        clauses: list[str] = []
        params: list[Any] = []
        for key, value in where.items():
            if key not in self.queryable_columns:
                raise ValueError(
                    f"cannot filter {self.table} on {key!r}; "
                    f"queryable: {sorted(self.queryable_columns)}"
                )
            clauses.append(f"{key} = %s")
            params.append(value)
        return clauses, params


_REGISTRY: dict[str, EntityMapper[Any]] = {}


def register_mapper(mapper: EntityMapper[Any]) -> EntityMapper[Any]:
    """Register ``mapper`` under its entity class name. Returns the mapper."""
    name = mapper.entity_cls.__name__
    _REGISTRY[name] = mapper
    return mapper


def _registry() -> dict[str, EntityMapper[Any]]:
    return _REGISTRY
