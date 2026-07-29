"""Workspace membership: list, invite, change role, remove.

Without these, multi-user is theoretical: the only way to give a second person
access was an INSERT by hand, so a workspace was permanently limited to whoever
created it. Roles and the `members` table already existed — this is the missing
surface over them.

Every route lives under `/api/workspaces/{workspace_id}/...`, so the app-level
middleware has already authorized the caller (read for GET, one of
`MUTATION_ROLES` for the rest) before a handler runs. Membership changes add one
further check on top: they require `admin` specifically, since an experimenter
who could grant roles could promote themselves.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException

from selfevals.api.auth import UserHeader, resolve_user_id
from selfevals.api.deps import AppDeps
from selfevals.api.schemas import (
    InviteMemberRequest,
    MemberResponse,
    UpdateMemberRoleRequest,
)
from selfevals.schemas.enums import Role
from selfevals.schemas.workspace import Member
from selfevals.storage.errors import EntityNotFoundError
from selfevals.storage.interface import ListFilter, StorageInterface


def _require_admin(storage: StorageInterface, *, workspace_id: str, user_id: str) -> None:
    """Membership changes are admin-only, above the middleware's write check.

    Skipped in `local` mode, which has no identity to check — matching how
    `authorize_workspace` treats that mode. Any other mode enforces it.
    """
    from selfevals.api.auth import auth_mode

    if auth_mode() == "local":
        return
    roles = storage.workspace_member_roles(workspace_id=workspace_id, user_id=user_id)
    if not roles or Role.ADMIN not in roles:
        raise HTTPException(status_code=403, detail="only workspace admins can manage members")


def _as_response(member: Member) -> MemberResponse:
    return MemberResponse(
        id=member.id,
        user_id=member.user_id,
        role=member.role,
        invited_by=member.invited_by,
        created_at=member.created_at,
    )


def register(app: FastAPI, deps: AppDeps) -> None:
    @app.get(
        "/api/workspaces/{workspace_id}/members",
        response_model=list[MemberResponse],
        tags=["members"],
    )
    def list_members(
        workspace_id: str,
        storage: StorageInterface = Depends(deps.storage),
    ) -> list[MemberResponse]:
        """Everyone with access, one row per (user, role) pair."""
        with storage.open(workspace_id) as scope:
            members = [m for m in scope.list_entities(Member, ListFilter()) if isinstance(m, Member)]
        members.sort(key=lambda m: (m.user_id, m.role.value))
        return [_as_response(m) for m in members]

    @app.post(
        "/api/workspaces/{workspace_id}/members",
        response_model=MemberResponse,
        status_code=201,
        tags=["members"],
    )
    def invite_member(
        workspace_id: str,
        body: InviteMemberRequest,
        storage: StorageInterface = Depends(deps.storage),
        user: UserHeader = None,
    ) -> MemberResponse:
        """Grant a user a role in this workspace.

        Takes a `user_id` rather than an email because identity here is still
        whatever string the deployment authenticates with — a header value under
        `header` mode, a real user id once people log in. Resolving emails would
        assume every member has a `users` row, which older deployments don't.
        """
        caller = resolve_user_id(user)
        _require_admin(storage, workspace_id=workspace_id, user_id=caller)

        existing = storage.workspace_member_roles(
            workspace_id=workspace_id, user_id=body.user_id
        )
        if existing is None:
            raise HTTPException(status_code=404, detail="workspace not found")
        if body.role in existing:
            raise HTTPException(
                status_code=409, detail=f"{body.user_id} already has role {body.role.value}"
            )

        member = Member(
            id=Member.make_id(),
            workspace_id=workspace_id,
            user_id=body.user_id,
            role=body.role,
            invited_by=caller,
        )
        with storage.open(workspace_id) as scope:
            scope.put_entity(member)
        return _as_response(member)

    @app.patch(
        "/api/workspaces/{workspace_id}/members/{member_id}",
        response_model=MemberResponse,
        tags=["members"],
    )
    def update_member_role(
        workspace_id: str,
        member_id: str,
        body: UpdateMemberRoleRequest,
        storage: StorageInterface = Depends(deps.storage),
        user: UserHeader = None,
    ) -> MemberResponse:
        caller = resolve_user_id(user)
        _require_admin(storage, workspace_id=workspace_id, user_id=caller)

        with storage.open(workspace_id) as scope:
            try:
                member = scope.get_entity(Member, member_id)
            except EntityNotFoundError as exc:
                raise HTTPException(status_code=404, detail="member not found") from exc
            assert isinstance(member, Member)

            if member.role == Role.ADMIN and body.role != Role.ADMIN:
                _refuse_if_last_admin(storage, workspace_id=workspace_id, member=member)

            member.role = body.role
            scope.put_entity(member)
        return _as_response(member)

    @app.delete(
        "/api/workspaces/{workspace_id}/members/{member_id}",
        status_code=204,
        tags=["members"],
    )
    def remove_member(
        workspace_id: str,
        member_id: str,
        storage: StorageInterface = Depends(deps.storage),
        user: UserHeader = None,
    ) -> None:
        """Revoke one role. Removing the last admin is refused."""
        caller = resolve_user_id(user)
        _require_admin(storage, workspace_id=workspace_id, user_id=caller)

        with storage.open(workspace_id) as scope:
            try:
                member = scope.get_entity(Member, member_id)
            except EntityNotFoundError as exc:
                raise HTTPException(status_code=404, detail="member not found") from exc
            assert isinstance(member, Member)

            if member.role == Role.ADMIN:
                _refuse_if_last_admin(storage, workspace_id=workspace_id, member=member)
            scope.delete_entity(Member, member_id)


def _refuse_if_last_admin(
    storage: StorageInterface, *, workspace_id: str, member: Member
) -> None:
    """Block the change that would leave a workspace unadministrable.

    Nothing else can restore an admin — there is no superuser and no recovery
    flow — so a workspace whose last admin is demoted or removed is stuck
    forever. Cheaper to refuse than to build the escape hatch.
    """
    with storage.open(workspace_id) as scope:
        admins = [
            m
            for m in scope.list_entities(Member, ListFilter())
            if isinstance(m, Member) and m.role == Role.ADMIN and m.id != member.id
        ]
    if not admins:
        raise HTTPException(
            status_code=409,
            detail="cannot remove the last admin; promote another member first",
        )
