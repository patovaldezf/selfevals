"""Workspace and Member: multi-tenant primitives.

A `Workspace` is the unit of isolation for every other entity. Its `id` is
a ULID; the human-facing `slug` is mutable and unique within a single
deployment. Membership is by `Member` rows, each carrying a `Role`.
"""

from __future__ import annotations

import re
from typing import Annotated, ClassVar

from pydantic import Field, StringConstraints, field_validator, model_validator

from selfevals.schemas._base import BaseEntity, NonEmptyStr, SelfEvalsModel
from selfevals.schemas.enums import Role

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
Slug = Annotated[str, StringConstraints(min_length=1, max_length=63)]


class WorkspaceSettings(SelfEvalsModel):
    """Per-workspace overrides; deliberately small for MVP."""

    default_runtime: str = "offline"
    retention_days: int = Field(default=365, ge=1, le=3650)


class Workspace(BaseEntity):
    """Top-level tenant boundary.

    `workspace_id` on every other entity must equal the owning Workspace's
    `id` — never its slug.
    """

    _id_prefix: ClassVar[str] = "ws"

    slug: Slug
    name: NonEmptyStr
    description: str | None = None
    owner_id: NonEmptyStr | None = None
    settings: WorkspaceSettings = Field(default_factory=WorkspaceSettings)

    @field_validator("slug")
    @classmethod
    def _slug_pattern(cls, value: str) -> str:
        if not _SLUG_RE.match(value):
            raise ValueError(
                "slug must be 1-63 chars: lowercase letters, digits, '-' or '_', "
                "starting with a letter or digit"
            )
        return value

    @model_validator(mode="after")
    def _workspace_self_id_consistency(self) -> Workspace:
        # A Workspace's own workspace_id must equal its id — it is its own tenant.
        if self.workspace_id != self.id:
            raise ValueError("Workspace.workspace_id must equal its own id")
        return self


class Member(BaseEntity):
    """User-in-workspace assignment with a role."""

    _id_prefix: ClassVar[str] = "mbr"

    user_id: NonEmptyStr
    role: Role
    invited_by: NonEmptyStr | None = None
