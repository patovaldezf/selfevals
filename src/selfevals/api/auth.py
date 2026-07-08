"""Authentication and authorization seams for the FastAPI surface.

The current implementation keeps local-development compatibility with the
legacy ``X-SelfEvals-User`` header while making that behavior explicit and
centralized. Shared deployments should set ``SELFEVALS_AUTH_MODE`` to a real
mode before exposing the API.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Annotated

from fastapi import Header, HTTPException

from selfevals.schemas.enums import Role
from selfevals.storage.interface import StorageInterface

USER_HEADER = "X-SelfEvals-User"
LOCAL_USER_ID = "local"

READ_ROLES: frozenset[Role] = frozenset(Role)
MUTATION_ROLES: frozenset[Role] = frozenset(
    {Role.EXPERIMENTER, Role.MAINTAINER, Role.ADMIN}
)

UserHeader = Annotated[
    str | None,
    Header(alias=USER_HEADER, description="Development user id; replace in shared auth mode."),
]


@dataclass(frozen=True)
class Principal:
    """Authenticated caller identity used by API dependencies."""

    user_id: str
    auth_mode: str


def auth_mode() -> str:
    return os.environ.get("SELFEVALS_AUTH_MODE", "local").strip().lower() or "local"


def resolve_principal(user: str | None) -> Principal:
    """Resolve the current caller.

    ``local`` mode preserves the historical developer experience. Any stricter
    mode must receive an explicit user header until a token/session provider is
    wired in.
    """
    mode = auth_mode()
    if mode == "local":
        return Principal(user_id=user or LOCAL_USER_ID, auth_mode=mode)
    if user:
        return Principal(user_id=user, auth_mode=mode)
    raise HTTPException(status_code=401, detail="authentication required")


def resolve_user_id(user: str | None) -> str:
    return resolve_principal(user).user_id


def authorize_workspace(
    storage: StorageInterface,
    *,
    workspace_id: str,
    user: str | None,
    write: bool,
) -> Principal:
    """Authorize a caller against a workspace.

    Local mode keeps the development experience unchanged. Stricter modes
    require an explicit caller and a matching Member row; writes require one of
    the mutation-capable roles.
    """
    principal = resolve_principal(user)
    if principal.auth_mode == "local":
        return principal

    allowed_roles = MUTATION_ROLES if write else READ_ROLES
    roles = storage.workspace_member_roles(workspace_id=workspace_id, user_id=principal.user_id)
    if roles is None:
        raise HTTPException(status_code=404, detail="workspace not found")
    if any(role in allowed_roles for role in roles):
        return principal
    raise HTTPException(status_code=403, detail="workspace access denied")


def readable_workspace_ids(
    storage: StorageInterface,
    *,
    candidate_ids: list[str],
    user: str | None,
) -> set[str] | None:
    """Filter ``candidate_ids`` to the workspaces the caller may read.

    Returns ``None`` in local mode, meaning "no filtering needed" — callers
    should treat that as "all candidates are readable". In stricter modes,
    returns the subset of ``candidate_ids`` for which the caller has a
    read-capable role.
    """
    principal = resolve_principal(user)
    if principal.auth_mode == "local":
        return None
    return {
        workspace_id
        for workspace_id in candidate_ids
        if any(
            role in READ_ROLES
            for role in (
                storage.workspace_member_roles(
                    workspace_id=workspace_id, user_id=principal.user_id
                )
                or []
            )
        )
    }
