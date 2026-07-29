"""Authentication and authorization seams for the FastAPI surface.

Three modes, selected by ``SELFEVALS_AUTH_MODE``:

- ``local`` (default): the historical developer experience, no identity
  enforcement.
- ``header``: trusts the caller-supplied ``X-SelfEvals-User`` header as-is.
  Suitable only behind a trusted internal bridge that itself authenticates
  the caller before forwarding the header.
- ``token``: the caller-supplied header must be a signed token issued by
  :mod:`selfevals.api.tokens` (HMAC-SHA256, ``SELFEVALS_AUTH_SECRET``).
  Forged or expired tokens are rejected. This is the mode to use before any
  untrusted shared deployment.
"""

from __future__ import annotations

import hmac
import os
from dataclasses import dataclass
from typing import Annotated

from fastapi import Header, HTTPException

from selfevals.api.tokens import TokenError, verify_token
from selfevals.schemas.enums import Role
from selfevals.storage.interface import StorageInterface

USER_HEADER = "X-SelfEvals-User"
LOCAL_USER_ID = "local"
OPERATOR_SECRET_HEADER = "X-SelfEvals-Operator-Secret"

READ_ROLES: frozenset[Role] = frozenset(Role)
MUTATION_ROLES: frozenset[Role] = frozenset(
    {Role.EXPERIMENTER, Role.MAINTAINER, Role.ADMIN}
)

UserHeader = Annotated[
    str | None,
    Header(
        alias=USER_HEADER,
        description=(
            "Caller identity. In `local`/`header` mode: a plain user id. "
            "In `token` mode: a signed token from `selfevals.api.tokens.issue_token`."
        ),
    ),
]


@dataclass(frozen=True)
class Principal:
    """Authenticated caller identity used by API dependencies."""

    user_id: str
    auth_mode: str


AUTH_MODES: frozenset[str] = frozenset({"local", "header", "token"})


def auth_mode() -> str:
    """The configured auth mode, or raise if it isn't one we know.

    Validating here is a security control, not tidiness. Every unrecognized
    value used to fall through to the `header` branch — i.e. "trust whatever the
    caller says they are". So `SELFEVALS_AUTH_MODE=tokne` silently disabled
    authentication on a deployment that believed it had enabled it. A typo must
    fail loudly instead.
    """
    mode = os.environ.get("SELFEVALS_AUTH_MODE", "local").strip().lower() or "local"
    if mode not in AUTH_MODES:
        raise HTTPException(
            status_code=500,
            detail=(
                f"invalid SELFEVALS_AUTH_MODE {mode!r}; expected one of "
                f"{sorted(AUTH_MODES)}"
            ),
        )
    return mode


def resolve_principal(user: str | None) -> Principal:
    """Resolve the current caller.

    ``local`` mode preserves the historical developer experience. ``header``
    mode trusts the caller-supplied user id as-is. ``token`` mode requires the
    header to carry a signed, unexpired token and resolves the principal from
    its verified payload instead of the raw string.
    """
    mode = auth_mode()
    if mode == "local":
        return Principal(user_id=user or LOCAL_USER_ID, auth_mode=mode)
    if mode == "token":
        if not user:
            raise HTTPException(status_code=401, detail="authentication required")
        try:
            verified = verify_token(user)
        except TokenError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        return Principal(user_id=verified.user_id, auth_mode=mode)
    # `header`: the only remaining mode (auth_mode() rejects anything else).
    # Explicit rather than a fallthrough, so adding a mode can't silently
    # inherit "trust the header".
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


def authorize_operator(secret: str | None) -> None:
    """Authorize a server-operator action (issuing session tokens).

    Requires the caller to present ``SELFEVALS_AUTH_SECRET`` verbatim via
    ``X-SelfEvals-Operator-Secret``. Only meaningful in ``token`` mode — in
    ``local``/``header`` mode there is no signed-token concept to bootstrap.
    """
    if auth_mode() != "token":
        raise HTTPException(
            status_code=400, detail="session tokens are only issued in token auth mode"
        )
    configured = os.environ.get("SELFEVALS_AUTH_SECRET", "").strip()
    if not configured or not secret or not hmac.compare_digest(configured, secret):
        raise HTTPException(status_code=401, detail="operator secret required")
