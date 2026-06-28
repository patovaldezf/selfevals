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

USER_HEADER = "X-SelfEvals-User"
LOCAL_USER_ID = "local"

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
