"""HTTP-facing Arena operations: thin envelopes around `arena/service.py`.

Handlers in `api/app.py` call these, catching `ArenaOpError` (→ 422) and
`EntityNotFoundError` (→ 404). All domain logic (worktrees, spec building,
launch dispatch) lives in `arena.service`; this module only translates
between wire schemas and service dataclasses/entities.
"""

from __future__ import annotations

from selfevals._errors import SelfEvalsUserError
from selfevals.api.schemas import (
    ArenaBudgetView,
    ArenaResponse,
    ArenaRoundResponse,
    ArenaVariantResponse,
    CreateArenaRequest,
    GitRefsResponse,
    GitRefView,
    LaunchRoundRequest,
    PromoteVariantRequest,
    PromoteVariantResponse,
    RegisterVariantRequest,
    RoundEntryView,
)
from selfevals.arena import service as arena_service
from selfevals.arena.worktrees import WorktreeError, list_refs
from selfevals.schemas.arena import Arena, ArenaRound, ArenaVariant
from selfevals.storage.errors import EntityNotFoundError
from selfevals.storage.interface import ListFilter, StorageInterface


class ArenaOpError(Exception):
    """An Arena operation could not be completed for a user-correctable reason."""


def _arena_view(arena: Arena) -> ArenaResponse:
    return ArenaResponse(
        id=arena.id,
        workspace_id=arena.workspace_id,
        name=arena.name,
        goal=arena.goal,
        repo_path=arena.repo_path,
        agent_command=arena.agent_command,
        objective_metric=arena.objective_metric,
        dataset_id=arena.dataset_id,
        budget=ArenaBudgetView(**arena.budget.model_dump()),
        current_round=arena.current_round,
        state=str(arena.state),
        winner_variant_id=arena.winner_variant_id,
    )


def _variant_view(variant: ArenaVariant) -> ArenaVariantResponse:
    return ArenaVariantResponse(
        id=variant.id,
        arena_id=variant.arena_id,
        name=variant.name,
        git_ref=variant.git_ref,
        resolved_sha=variant.resolved_sha,
        worktree_path=variant.worktree_path,
        hypothesis=variant.hypothesis,
        created_in_round=variant.created_in_round,
        state=str(variant.state),
        error=variant.error,
    )


def _round_view(round_: ArenaRound) -> ArenaRoundResponse:
    return ArenaRoundResponse(
        id=round_.id,
        arena_id=round_.arena_id,
        index=round_.index,
        entries=[
            RoundEntryView(
                variant_id=e.variant_id,
                experiment_id=e.experiment_id,
                run_job_id=e.run_job_id,
                status=e.status,
            )
            for e in round_.entries
        ],
        reps=round_.reps,
        state=str(round_.state),
    )


def create_arena(
    storage: StorageInterface, *, workspace_id: str, body: CreateArenaRequest
) -> ArenaResponse:
    req = arena_service.CreateArenaRequest(
        name=body.name,
        goal=body.goal,
        repo_path=body.repo_path,
        agent_command=body.agent_command,
        spec_template=body.spec_template,
        objective_metric=body.objective_metric,
        agent_env=body.agent_env,
        dataset_id=body.dataset_id,
        budget=body.budget.model_dump() if body.budget is not None else None,
    )
    try:
        arena = arena_service.create_arena(storage, workspace_id=workspace_id, req=req)
    except SelfEvalsUserError as exc:
        raise ArenaOpError(str(exc)) from exc
    return _arena_view(arena)


def get_arena(storage: StorageInterface, *, workspace_id: str, arena_id: str) -> ArenaResponse:
    with storage.open(workspace_id) as scope:
        arena = scope.get_entity(Arena, arena_id)
    assert isinstance(arena, Arena)
    return _arena_view(arena)


def list_arenas(storage: StorageInterface, *, workspace_id: str) -> list[ArenaResponse]:
    with storage.open(workspace_id) as scope:
        arenas = scope.list_entities(Arena, ListFilter(order_by="created_at", order_desc=True))
    return [_arena_view(a) for a in arenas if isinstance(a, Arena)]


def delete_arena(storage: StorageInterface, *, workspace_id: str, arena_id: str) -> None:
    arena_service.cleanup_arena(storage, workspace_id=workspace_id, arena_id=arena_id)


def register_variant(
    storage: StorageInterface,
    *,
    storage_url: str,
    workspace_id: str,
    arena_id: str,
    body: RegisterVariantRequest,
) -> ArenaVariantResponse:
    req = arena_service.RegisterVariantRequest(
        name=body.name,
        git_ref=body.git_ref,
        setup_command=body.setup_command,
        env_overrides=body.env_overrides,
        hypothesis=body.hypothesis,
    )
    try:
        variant = arena_service.register_variant(
            storage, storage_url=storage_url, workspace_id=workspace_id, arena_id=arena_id, req=req
        )
    except SelfEvalsUserError as exc:
        raise ArenaOpError(str(exc)) from exc
    return _variant_view(variant)


def list_variants(
    storage: StorageInterface, *, workspace_id: str, arena_id: str
) -> list[ArenaVariantResponse]:
    with storage.open(workspace_id) as scope:
        variants = scope.list_entities(ArenaVariant, ListFilter(where={"arena_id": arena_id}))
    return [_variant_view(v) for v in variants if isinstance(v, ArenaVariant)]


def launch_round(
    storage: StorageInterface,
    *,
    storage_url: str,
    workspace_id: str,
    arena_id: str,
    body: LaunchRoundRequest,
) -> ArenaRoundResponse:
    req = arena_service.LaunchRoundRequest(variant_ids=body.variant_ids, reps=body.reps)
    try:
        round_ = arena_service.launch_round(
            storage, storage_url=storage_url, workspace_id=workspace_id, arena_id=arena_id, req=req
        )
    except SelfEvalsUserError as exc:
        raise ArenaOpError(str(exc)) from exc
    return _round_view(round_)


def list_rounds(
    storage: StorageInterface, *, workspace_id: str, arena_id: str
) -> list[ArenaRoundResponse]:
    with storage.open(workspace_id) as scope:
        rounds = scope.list_entities(
            ArenaRound, ListFilter(where={"arena_id": arena_id}, order_by="index", order_desc=False)
        )
    return [_round_view(r) for r in rounds if isinstance(r, ArenaRound)]


def get_round(
    storage: StorageInterface, *, workspace_id: str, arena_id: str, round_id: str
) -> ArenaRoundResponse:
    refreshed = arena_service.refresh_round_status(storage, workspace_id=workspace_id, round_id=round_id)
    if refreshed.arena_id != arena_id:
        raise EntityNotFoundError("ArenaRound", round_id, workspace_id)
    return _round_view(refreshed)


def promote_variant(
    storage: StorageInterface,
    *,
    workspace_id: str,
    arena_id: str,
    body: PromoteVariantRequest,
) -> PromoteVariantResponse:
    try:
        result = arena_service.promote_winner(
            storage, workspace_id=workspace_id, arena_id=arena_id, variant_id=body.variant_id
        )
    except SelfEvalsUserError as exc:
        raise ArenaOpError(str(exc)) from exc
    sha = result["resolved_sha"]
    commands = result["suggested_commands"]
    assert sha is None or isinstance(sha, str)
    assert isinstance(commands, list)
    return PromoteVariantResponse(
        winner_variant_id=str(result["winner_variant_id"]),
        git_ref=str(result["git_ref"]),
        resolved_sha=sha,
        suggested_commands=[str(c) for c in commands],
    )


def git_refs(repo_path: str) -> GitRefsResponse:
    try:
        refs = list_refs(repo_path)
    except WorktreeError as exc:
        raise ArenaOpError(str(exc)) from exc
    return GitRefsResponse(
        repo_path=repo_path, refs=[GitRefView(name=r.name, sha=r.sha) for r in refs]
    )
