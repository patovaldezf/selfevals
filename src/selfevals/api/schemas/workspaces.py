"""Workspace list/detail/create response shapes."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from selfevals.schemas.enums import Role


class WorkspaceSummary(BaseModel):
    id: str
    slug: str
    name: str
    description: str | None = None
    owner_id: str | None = None
    created_at: datetime
    experiment_count: int = 0
    last_run_at: datetime | None = None


class WorkspaceListResponse(BaseModel):
    workspaces: list[WorkspaceSummary]


class WorkspaceResponse(BaseModel):
    id: str
    slug: str
    name: str
    description: str | None = None
    owner_id: str | None = None
    created_at: datetime
    experiment_count: int
    recent_health: float | None = Field(
        default=None,
        description="Fraction of recent experiments that landed on keep_candidate.",
    )


class CreateWorkspaceRequest(BaseModel):
    slug: str = Field(min_length=1, max_length=63)
    name: str | None = None
    description: str | None = None


class MemberResponse(BaseModel):
    """One (user, role) grant. A user with two roles has two rows."""

    id: str
    user_id: str
    role: Role
    invited_by: str | None = None
    created_at: datetime


class InviteMemberRequest(BaseModel):
    """Grant a role to a user id.

    Not an email: identity is whatever string the deployment authenticates
    with, and older deployments have members that predate the `users` table.
    """

    user_id: str = Field(min_length=1, max_length=255)
    role: Role = Role.VIEWER


class UpdateMemberRoleRequest(BaseModel):
    role: Role
