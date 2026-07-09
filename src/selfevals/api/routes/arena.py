"""Arena (parallel experiments across git-worktree code variants)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Response

from selfevals.api import arena_ops
from selfevals.api.arena_ops import ArenaOpError
from selfevals.api.auth import UserHeader
from selfevals.api.deps import AppDeps
from selfevals.api.schemas import (
    ArenaResponse,
    ArenaRoundResponse,
    ArenaVariantResponse,
    CreateArenaRequest,
    GitRefsResponse,
    LaunchRoundRequest,
    PromoteVariantRequest,
    PromoteVariantResponse,
    RegisterVariantRequest,
    RoundCostEstimateResponse,
)
from selfevals.storage.errors import EntityNotFoundError
from selfevals.storage.interface import StorageInterface


def register(app: FastAPI, deps: AppDeps) -> None:
    @app.post(
        "/api/workspaces/{workspace_id}/arenas",
        response_model=ArenaResponse,
        status_code=201,
        tags=["arena"],
    )
    def arenas_create(
        workspace_id: str,
        body: CreateArenaRequest,
        storage: StorageInterface = Depends(deps.storage),
        _user: UserHeader = None,
    ) -> ArenaResponse:
        try:
            return arena_ops.create_arena(storage, workspace_id=workspace_id, body=body)
        except ArenaOpError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get(
        "/api/workspaces/{workspace_id}/arenas",
        response_model=list[ArenaResponse],
        tags=["arena"],
    )
    def arenas_list(
        workspace_id: str,
        storage: StorageInterface = Depends(deps.storage),
        _user: UserHeader = None,
    ) -> list[ArenaResponse]:
        return arena_ops.list_arenas(storage, workspace_id=workspace_id)

    @app.get(
        "/api/workspaces/{workspace_id}/arenas/{arena_id}",
        response_model=ArenaResponse,
        tags=["arena"],
    )
    def arenas_get(
        workspace_id: str,
        arena_id: str,
        storage: StorageInterface = Depends(deps.storage),
        _user: UserHeader = None,
    ) -> ArenaResponse:
        try:
            return arena_ops.get_arena(storage, workspace_id=workspace_id, arena_id=arena_id)
        except EntityNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"arena {arena_id} not found") from exc

    @app.delete(
        "/api/workspaces/{workspace_id}/arenas/{arena_id}",
        status_code=204,
        tags=["arena"],
    )
    def arenas_delete(
        workspace_id: str,
        arena_id: str,
        storage: StorageInterface = Depends(deps.storage),
        _user: UserHeader = None,
    ) -> Response:
        try:
            arena_ops.delete_arena(storage, workspace_id=workspace_id, arena_id=arena_id)
        except EntityNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"arena {arena_id} not found") from exc
        return Response(status_code=204)

    @app.post(
        "/api/workspaces/{workspace_id}/arenas/{arena_id}/variants",
        response_model=ArenaVariantResponse,
        status_code=202,
        tags=["arena"],
    )
    def arena_variants_create(
        workspace_id: str,
        arena_id: str,
        body: RegisterVariantRequest,
        storage: StorageInterface = Depends(deps.storage),
        _user: UserHeader = None,
    ) -> ArenaVariantResponse:
        try:
            return arena_ops.register_variant(
                storage,
                storage_url=deps.storage_url,
                workspace_id=workspace_id,
                arena_id=arena_id,
                body=body,
            )
        except EntityNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"arena {arena_id} not found") from exc
        except ArenaOpError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get(
        "/api/workspaces/{workspace_id}/arenas/{arena_id}/variants",
        response_model=list[ArenaVariantResponse],
        tags=["arena"],
    )
    def arena_variants_list(
        workspace_id: str,
        arena_id: str,
        storage: StorageInterface = Depends(deps.storage),
        _user: UserHeader = None,
    ) -> list[ArenaVariantResponse]:
        return arena_ops.list_variants(storage, workspace_id=workspace_id, arena_id=arena_id)

    @app.post(
        "/api/workspaces/{workspace_id}/arenas/{arena_id}/rounds",
        response_model=ArenaRoundResponse,
        status_code=202,
        tags=["arena"],
    )
    def arena_rounds_create(
        workspace_id: str,
        arena_id: str,
        body: LaunchRoundRequest,
        storage: StorageInterface = Depends(deps.storage),
        _user: UserHeader = None,
    ) -> ArenaRoundResponse:
        try:
            return arena_ops.launch_round(
                storage,
                storage_url=deps.storage_url,
                workspace_id=workspace_id,
                arena_id=arena_id,
                body=body,
            )
        except EntityNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"arena {arena_id} not found") from exc
        except ArenaOpError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get(
        "/api/workspaces/{workspace_id}/arenas/{arena_id}/estimate-cost",
        response_model=RoundCostEstimateResponse,
        tags=["arena"],
    )
    def arena_estimate_cost(
        workspace_id: str,
        arena_id: str,
        variant_ids: Annotated[
            str | None, Query(description="Comma-separated variant ids; default: all ready variants.")
        ] = None,
        reps: Annotated[int, Query(ge=1)] = 1,
        storage: StorageInterface = Depends(deps.storage),
        _user: UserHeader = None,
    ) -> RoundCostEstimateResponse:
        try:
            return arena_ops.estimate_round_cost(
                storage,
                workspace_id=workspace_id,
                arena_id=arena_id,
                variant_ids=variant_ids.split(",") if variant_ids else None,
                reps=reps,
            )
        except EntityNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"arena {arena_id} not found") from exc

    @app.get(
        "/api/workspaces/{workspace_id}/arenas/{arena_id}/rounds",
        response_model=list[ArenaRoundResponse],
        tags=["arena"],
    )
    def arena_rounds_list(
        workspace_id: str,
        arena_id: str,
        storage: StorageInterface = Depends(deps.storage),
        _user: UserHeader = None,
    ) -> list[ArenaRoundResponse]:
        return arena_ops.list_rounds(storage, workspace_id=workspace_id, arena_id=arena_id)

    @app.get(
        "/api/workspaces/{workspace_id}/arenas/{arena_id}/rounds/{round_id}",
        response_model=ArenaRoundResponse,
        tags=["arena"],
    )
    def arena_rounds_get(
        workspace_id: str,
        arena_id: str,
        round_id: str,
        storage: StorageInterface = Depends(deps.storage),
        _user: UserHeader = None,
    ) -> ArenaRoundResponse:
        try:
            return arena_ops.get_round(
                storage, workspace_id=workspace_id, arena_id=arena_id, round_id=round_id
            )
        except EntityNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"round {round_id} not found") from exc

    @app.post(
        "/api/workspaces/{workspace_id}/arenas/{arena_id}/promote",
        response_model=PromoteVariantResponse,
        tags=["arena"],
    )
    def arena_promote(
        workspace_id: str,
        arena_id: str,
        body: PromoteVariantRequest,
        storage: StorageInterface = Depends(deps.storage),
        _user: UserHeader = None,
    ) -> PromoteVariantResponse:
        try:
            return arena_ops.promote_variant(
                storage, workspace_id=workspace_id, arena_id=arena_id, body=body
            )
        except EntityNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ArenaOpError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get(
        "/api/workspaces/{workspace_id}/arenas/{arena_id}/bundle",
        tags=["arena"],
    )
    def arena_bundle(
        workspace_id: str,
        arena_id: str,
        round: Annotated[int | None, Query(ge=0, description="Round index; defaults to latest.")] = None,
        storage: StorageInterface = Depends(deps.storage),
        _user: UserHeader = None,
    ) -> dict[str, Any]:
        # Same pattern as the error-analysis bundle: pass the arena.schemas
        # Pydantic model through as JSON so the contract lives in one place.
        from selfevals.arena.bundle import build_bundle

        try:
            bundle = build_bundle(
                storage, workspace_id=workspace_id, arena_id=arena_id, round_index=round
            )
            return bundle.model_dump(mode="json")
        except EntityNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"arena {arena_id} not found") from exc

    @app.get(
        "/api/workspaces/{workspace_id}/git/refs",
        response_model=GitRefsResponse,
        tags=["arena"],
    )
    def arena_git_refs(
        workspace_id: str,
        repo_path: str = Query(..., description="Absolute path to a git repo on the server."),
        _user: UserHeader = None,
    ) -> GitRefsResponse:
        try:
            return arena_ops.git_refs(repo_path)
        except ArenaOpError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
