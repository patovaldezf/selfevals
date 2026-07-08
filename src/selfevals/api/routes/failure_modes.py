"""Failure-mode taxonomy CRUD (candidate → official → retired lifecycle)."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query

from selfevals.api.auth import UserHeader
from selfevals.api.deps import AppDeps
from selfevals.api.failure_mode_ops import (
    FailureModeNotFoundError,
    FailureModeOpError,
    edit_failure_mode,
    list_failure_modes,
    merge_failure_modes,
    promote_failure_mode,
    retire_failure_mode,
)
from selfevals.api.schemas import (
    EditFailureModeRequest,
    FailureModeListResponse,
    FailureModeResponse,
    MergeFailureModeRequest,
)
from selfevals.storage.interface import StorageInterface


def register(app: FastAPI, deps: AppDeps) -> None:
    @app.get(
        "/api/workspaces/{workspace_id}/failure-modes",
        response_model=FailureModeListResponse,
        tags=["failure-modes"],
    )
    def failure_modes_index(
        workspace_id: str,
        storage: StorageInterface = Depends(deps.storage),
        status: Annotated[
            str | None,
            Query(description="Filter by status (candidate/official/retired)."),
        ] = None,
        _user: UserHeader = None,
    ) -> FailureModeListResponse:
        try:
            items = list_failure_modes(storage, workspace_id=workspace_id, status=status)
            return FailureModeListResponse(items=items)
        finally:
            storage.close()

    @app.post(
        "/api/workspaces/{workspace_id}/failure-modes/{failure_mode_id}/promote",
        response_model=FailureModeResponse,
        tags=["failure-modes"],
    )
    def failure_modes_promote(
        workspace_id: str,
        failure_mode_id: str,
        storage: StorageInterface = Depends(deps.storage),
        _user: UserHeader = None,
    ) -> FailureModeResponse:
        try:
            return promote_failure_mode(storage, workspace_id=workspace_id, fm_id=failure_mode_id)
        except FailureModeNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            storage.close()

    @app.post(
        "/api/workspaces/{workspace_id}/failure-modes/{failure_mode_id}/retire",
        response_model=FailureModeResponse,
        tags=["failure-modes"],
    )
    def failure_modes_retire(
        workspace_id: str,
        failure_mode_id: str,
        storage: StorageInterface = Depends(deps.storage),
        _user: UserHeader = None,
    ) -> FailureModeResponse:
        try:
            return retire_failure_mode(storage, workspace_id=workspace_id, fm_id=failure_mode_id)
        except FailureModeNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            storage.close()

    @app.post(
        "/api/workspaces/{workspace_id}/failure-modes/{failure_mode_id}/merge",
        response_model=FailureModeResponse,
        tags=["failure-modes"],
    )
    def failure_modes_merge(
        workspace_id: str,
        failure_mode_id: str,
        body: MergeFailureModeRequest,
        storage: StorageInterface = Depends(deps.storage),
        _user: UserHeader = None,
    ) -> FailureModeResponse:
        try:
            return merge_failure_modes(
                storage,
                workspace_id=workspace_id,
                fm_id=failure_mode_id,
                into_id=body.into_id,
            )
        except FailureModeNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FailureModeOpError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            storage.close()

    @app.patch(
        "/api/workspaces/{workspace_id}/failure-modes/{failure_mode_id}",
        response_model=FailureModeResponse,
        tags=["failure-modes"],
    )
    def failure_modes_edit(
        workspace_id: str,
        failure_mode_id: str,
        body: EditFailureModeRequest,
        storage: StorageInterface = Depends(deps.storage),
        _user: UserHeader = None,
    ) -> FailureModeResponse:
        try:
            return edit_failure_mode(
                storage,
                workspace_id=workspace_id,
                fm_id=failure_mode_id,
                title=body.title,
                definition=body.definition,
            )
        except FailureModeNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            storage.close()
