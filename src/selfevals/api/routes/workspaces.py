"""Workspace listing, detail, and creation."""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException

from selfevals.api.auth import UserHeader, readable_workspace_ids, resolve_user_id
from selfevals.api.deps import AppDeps
from selfevals.api.queries import list_workspaces, workspace_detail
from selfevals.api.schemas import CreateWorkspaceRequest, WorkspaceListResponse, WorkspaceResponse
from selfevals.storage.interface import StorageInterface


def register(app: FastAPI, deps: AppDeps) -> None:
    @app.get(
        "/api/workspaces",
        response_model=WorkspaceListResponse,
        tags=["workspaces"],
    )
    def workspaces_index(
        storage: StorageInterface = Depends(deps.storage),
        _user: UserHeader = None,
    ) -> WorkspaceListResponse:
        try:
            summaries = list_workspaces(storage)
            allowed = readable_workspace_ids(
                storage, candidate_ids=[ws.id for ws in summaries], user=_user
            )
            if allowed is not None:
                summaries = [ws for ws in summaries if ws.id in allowed]
            return WorkspaceListResponse(workspaces=summaries)
        finally:
            storage.close()

    @app.get(
        "/api/workspaces/{workspace_id}",
        response_model=WorkspaceResponse,
        tags=["workspaces"],
    )
    def workspaces_show(
        workspace_id: str,
        storage: StorageInterface = Depends(deps.storage),
        _user: UserHeader = None,
    ) -> WorkspaceResponse:
        try:
            ws = workspace_detail(storage, workspace_id=workspace_id)
            if ws is None:
                raise HTTPException(status_code=404, detail="workspace not found")
            return ws
        finally:
            storage.close()

    @app.post(
        "/api/workspaces",
        response_model=WorkspaceResponse,
        status_code=201,
        tags=["workspaces"],
    )
    def workspaces_create(
        body: CreateWorkspaceRequest,
        storage: StorageInterface = Depends(deps.storage),
        user: UserHeader = None,
    ) -> WorkspaceResponse:
        from selfevals.storage.seed import seed_workspace

        try:
            seeded = seed_workspace(
                storage,
                slug=body.slug,
                name=body.name or body.slug,
                user_id=resolve_user_id(user),
                description=body.description,
                # Admin only — not every role. Admin already grants full
                # read+write, so the extra rows bought nothing while handing the
                # creator `auditor` too, collapsing the separation of duties that
                # role exists to provide. The CLI's `init` keeps the all-roles
                # default: that's a single-operator bootstrap, not a shared API.
                assign_all_roles=False,
            )
            ws = seeded.workspace
            return WorkspaceResponse(
                id=ws.id,
                slug=ws.slug,
                name=ws.name,
                description=ws.description,
                owner_id=ws.owner_id,
                created_at=ws.created_at,
                experiment_count=0,
                recent_health=None,
            )
        finally:
            storage.close()
