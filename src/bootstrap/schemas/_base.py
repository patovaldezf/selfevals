"""Base entity and shared reference types.

Every persistent bootstrap entity inherits from `BaseEntity`. The workspace
isolation invariant is enforced at the schema layer: `workspace_id` is
required and non-empty. Storage queries always filter by `workspace_id`;
constructing an entity without it is a hard error.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from bootstrap._internal.ids import new_prefixed_id
from bootstrap._internal.time import ensure_utc, utc_now

NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]


class BootstrapModel(BaseModel):
    """Base model with strict defaults for all bootstrap schemas."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        str_strip_whitespace=False,
        frozen=False,
    )


class EntityRef(BootstrapModel):
    """Lightweight reference to another entity by id + optional version."""

    id: NonEmptyStr
    version: int | None = Field(default=None, ge=1)


class BaseEntity(BootstrapModel):
    """Shared fields for every persistent entity.

    `id` is a prefixed ULID (e.g. `ws_01H...`). Subclasses set the prefix
    via the `_id_prefix` class variable used by `BaseEntity.make_id`.

    `workspace_id` is required and non-empty — enforces multi-tenant
    isolation from the schema layer.

    `version` is a monotonic int, incremented by storage on update.
    Artifacts that need reproducibility (Agent, Tool, Prompt, Dataset)
    additionally carry a `content_hash` defined on the subclass.
    """

    _id_prefix: ClassVar[str] = "ent"

    id: NonEmptyStr
    workspace_id: NonEmptyStr
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    deleted_at: datetime | None = None

    @field_validator("created_at", "updated_at", "deleted_at")
    @classmethod
    def _utc_only(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return ensure_utc(value)

    @classmethod
    def make_id(cls) -> str:
        """Generate a fresh prefixed ULID for this entity type."""
        return new_prefixed_id(cls._id_prefix)

    def model_dump_canonical(self) -> dict[str, Any]:
        """JSON-safe dump used for content hashing.

        Excludes mutable bookkeeping fields (`updated_at`, `deleted_at`,
        `version`) so the canonical hash is stable across cosmetic changes.
        """
        data = self.model_dump(mode="json", exclude={"updated_at", "deleted_at", "version"})
        return data
