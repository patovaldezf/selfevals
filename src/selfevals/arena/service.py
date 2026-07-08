"""Arena orchestration: the layer shared by the API and the CLI.

Owns the full lifecycle — create an Arena, register variants (git worktree
prepared in a background thread), launch a round (one child `Experiment` per
ready variant, reusing `launch_experiment_run` verbatim), and read back
composite status. Never generates or edits code; that stays the job of the
external coding agent driving Arena through `arena.bundle`.
"""

from __future__ import annotations

import copy
import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from selfevals._errors import SelfEvalsUserError
from selfevals.api.run_launcher import launch_experiment_run
from selfevals.api.schemas import RunExperimentRequest
from selfevals.arena.worktrees import (
    WorktreeError,
    ensure_worktree,
    prune,
    remove_worktree,
    resolve_ref,
    run_setup,
    worktrees_root,
)
from selfevals.repo.loader import LoaderError, build_spec_from_mapping
from selfevals.schemas.arena import Arena, ArenaRound, ArenaVariant, RoundEntry
from selfevals.schemas.enums import ArenaRoundState, ArenaState, ArenaVariantState, ExperimentState
from selfevals.schemas.experiment import Experiment
from selfevals.storage.factory import open_storage
from selfevals.storage.interface import ListFilter, StorageInterface

logger = logging.getLogger(__name__)

_TERMINAL_EXPERIMENT_STATES = {
    ExperimentState.COMPLETED,
    ExperimentState.ABORTED,
    ExperimentState.SUPERSEDED,
}


@dataclass(frozen=True)
class CreateArenaRequest:
    name: str
    goal: str
    repo_path: str
    agent_command: list[str]
    spec_template: dict[str, object]
    objective_metric: str
    agent_env: dict[str, str] | None = None
    dataset_id: str | None = None
    budget: dict[str, object] | None = None


@dataclass(frozen=True)
class RegisterVariantRequest:
    name: str
    git_ref: str
    setup_command: list[str] | None = None
    env_overrides: dict[str, str] | None = None
    hypothesis: str | None = None


@dataclass(frozen=True)
class LaunchRoundRequest:
    variant_ids: list[str] | None = None
    reps: int = 1


def create_arena(storage: StorageInterface, *, workspace_id: str, req: CreateArenaRequest) -> Arena:
    """Validate `req` and persist a new draft Arena.

    `spec_template` is dry-run validated by handing it to
    `build_spec_from_mapping` with a placeholder `agent:` block — this
    catches a malformed template (bad graders, missing dataset, invalid
    experiment fields) at creation time instead of at the first round.
    """
    from selfevals.schemas.arena import ArenaBudget

    _validate_spec_template(req.spec_template, workspace_id=workspace_id)

    arena = Arena(
        id=Arena.make_id(),
        workspace_id=workspace_id,
        name=req.name,
        goal=req.goal,
        repo_path=req.repo_path,
        agent_command=req.agent_command,
        agent_env=req.agent_env,
        spec_template=req.spec_template,
        dataset_id=req.dataset_id,
        objective_metric=req.objective_metric,
        budget=ArenaBudget.model_validate(req.budget or {}),
    )
    with storage.open(workspace_id) as scope:
        scope.put_entity(arena)
    return arena


def _validate_spec_template(spec_template: dict[str, object], *, workspace_id: str) -> None:
    probe = copy.deepcopy(spec_template)
    probe["agent"] = {"type": "cli", "command": ["true"]}
    try:
        build_spec_from_mapping(probe, workspace_id=workspace_id)
    except LoaderError as exc:
        raise SelfEvalsUserError(f"arena spec_template is invalid: {exc}") from exc


def register_variant(
    storage: StorageInterface,
    *,
    storage_url: str,
    workspace_id: str,
    arena_id: str,
    req: RegisterVariantRequest,
) -> ArenaVariant:
    """Register a variant against an existing git ref and start preparing it.

    Resolves `req.git_ref` to a commit SHA synchronously (fast, local) so a
    typo'd branch name fails the request immediately with a clear error.
    Worktree creation + `setup_command` run in a background daemon thread —
    the variant is visible right away in `preparing` state and flips to
    `ready`/`failed` once the thread finishes.
    """
    with storage.open(workspace_id) as scope:
        arena = scope.get_entity(Arena, arena_id)
        assert isinstance(arena, Arena)
        if arena.budget.max_variants is not None:
            existing = scope.list_entities(ArenaVariant, ListFilter(where={"arena_id": arena_id}))
            if len(existing) >= arena.budget.max_variants:
                raise SelfEvalsUserError(
                    f"arena {arena_id} already has {len(existing)} variants "
                    f"(budget.max_variants={arena.budget.max_variants})"
                )

        try:
            resolved_sha = resolve_ref(arena.repo_path, req.git_ref)
        except WorktreeError as exc:
            raise SelfEvalsUserError(str(exc)) from exc

        variant = ArenaVariant(
            id=ArenaVariant.make_id(),
            workspace_id=workspace_id,
            arena_id=arena_id,
            name=req.name,
            git_ref=req.git_ref,
            resolved_sha=resolved_sha,
            setup_command=req.setup_command,
            env_overrides=req.env_overrides,
            hypothesis=req.hypothesis,
            created_in_round=arena.current_round,
            state=ArenaVariantState.PREPARING,
        )
        scope.put_entity(variant)

    thread = threading.Thread(
        target=_prepare_variant,
        kwargs={
            "storage_url": storage_url,
            "workspace_id": workspace_id,
            "repo_path": arena.repo_path,
            "variant_id": variant.id,
        },
        name=f"arena-variant-{variant.id}",
        daemon=True,
    )
    thread.start()
    return variant


def _prepare_variant(*, storage_url: str, workspace_id: str, repo_path: str, variant_id: str) -> None:
    prep_storage = open_storage(storage_url)
    try:
        with prep_storage.open(workspace_id) as scope:
            variant = scope.get_entity(ArenaVariant, variant_id)
            assert isinstance(variant, ArenaVariant)
        assert variant.resolved_sha is not None
        dest = worktrees_root() / variant.arena_id / variant.id
        try:
            ensure_worktree(repo_path, variant.resolved_sha, dest)
            if variant.setup_command:
                run_setup(dest, variant.setup_command)
        except WorktreeError as exc:
            with prep_storage.open(workspace_id) as scope:
                variant = scope.get_entity(ArenaVariant, variant_id)
                assert isinstance(variant, ArenaVariant)
                variant.state = ArenaVariantState.FAILED
                variant.error = str(exc)
                scope.put_entity(variant)
            logger.warning("arena variant %s failed to prepare: %s", variant_id, exc)
            return
        with prep_storage.open(workspace_id) as scope:
            variant = scope.get_entity(ArenaVariant, variant_id)
            assert isinstance(variant, ArenaVariant)
            variant.worktree_path = str(dest)
            variant.state = ArenaVariantState.READY
            scope.put_entity(variant)
    finally:
        prep_storage.close()


def _build_variant_spec(arena: Arena, variant: ArenaVariant, round_index: int) -> dict[str, object]:
    """Clone `arena.spec_template` and inject this variant's agent block.

    The clone gets a fresh `experiment.id`/name per (variant, round) so each
    launch is its own single-iteration `Experiment` — no relaunching a
    previous experiment (the loop always starts at iteration 0).
    """
    spec = copy.deepcopy(arena.spec_template)
    experiment_block = dict(spec.get("experiment") or {})
    experiment_block["id"] = Experiment.make_id()
    experiment_block["name"] = f"{arena.name}/{variant.name}/r{round_index}"
    spec["experiment"] = experiment_block

    env = dict(arena.agent_env or {})
    env.update(variant.env_overrides or {})
    agent_block: dict[str, object] = {
        "type": "cli",
        "command": list(arena.agent_command),
        "cwd": variant.worktree_path,
    }
    if env:
        agent_block["env"] = env
    spec["agent"] = agent_block
    return spec


def launch_round(
    storage: StorageInterface,
    *,
    storage_url: str,
    workspace_id: str,
    arena_id: str,
    req: LaunchRoundRequest,
) -> ArenaRound:
    """Launch one round: every requested `ready` variant, run in parallel.

    Each variant becomes its own `POST .../experiments/run` dispatch via
    `launch_experiment_run` — the exact same durable-job/Redis/in-process
    dispatch path a normal experiment uses, so N variants racing is free
    reuse of existing infrastructure, not new scheduling code.
    """
    with storage.open(workspace_id) as scope:
        arena = scope.get_entity(Arena, arena_id)
        assert isinstance(arena, Arena)
        if arena.budget.max_rounds is not None and arena.current_round >= arena.budget.max_rounds:
            raise SelfEvalsUserError(
                f"arena {arena_id} already used {arena.current_round} rounds "
                f"(budget.max_rounds={arena.budget.max_rounds})"
            )

        all_variants = [
            v
            for v in scope.list_entities(ArenaVariant, ListFilter(where={"arena_id": arena_id}))
            if isinstance(v, ArenaVariant)
        ]
        by_id = {v.id: v for v in all_variants}
        if req.variant_ids is not None:
            selected = []
            for vid in req.variant_ids:
                if vid not in by_id:
                    raise SelfEvalsUserError(f"variant {vid} does not belong to arena {arena_id}")
                selected.append(by_id[vid])
        else:
            selected = [v for v in all_variants if v.state == ArenaVariantState.READY]
        not_ready = [v.id for v in selected if v.state != ArenaVariantState.READY]
        if not_ready:
            raise SelfEvalsUserError(f"variants not ready to run: {not_ready}")
        if not selected:
            raise SelfEvalsUserError(f"arena {arena_id} has no ready variants to launch")

        round_index = arena.current_round
        round_ = ArenaRound(
            id=ArenaRound.make_id(),
            workspace_id=workspace_id,
            arena_id=arena_id,
            index=round_index,
            reps=req.reps,
            state=ArenaRoundState.RUNNING,
            entries=[RoundEntry(variant_id=v.id) for v in selected],
        )
        scope.put_entity(round_)

    entries: list[RoundEntry] = []
    for variant in selected:
        spec = _build_variant_spec(arena, variant, round_index)
        response = launch_experiment_run(
            storage_url=storage_url,
            workspace_id=workspace_id,
            body=RunExperimentRequest(spec_inline=spec, dataset_id=arena.dataset_id, reps=req.reps),
        )
        entries.append(
            RoundEntry(
                variant_id=variant.id,
                experiment_id=response.experiment_id,
                run_job_id=response.job_id,
                status=response.state,
            )
        )

    with storage.open(workspace_id) as scope:
        stored_round = scope.get_entity(ArenaRound, round_.id)
        assert isinstance(stored_round, ArenaRound)
        stored_round.entries = entries
        scope.put_entity(stored_round)

        stored_arena = scope.get_entity(Arena, arena_id)
        assert isinstance(stored_arena, Arena)
        stored_arena.current_round = round_index + 1
        stored_arena.state = ArenaState.ACTIVE
        scope.put_entity(stored_arena)

    return stored_round


def refresh_round_status(storage: StorageInterface, *, workspace_id: str, round_id: str) -> ArenaRound:
    """Sync a round's entries against their child experiments' current state.

    Closes the round (marks it `completed`) once every entry's experiment
    has reached a terminal state; leaves it `running` otherwise. Called by
    the status/bundle read paths — never on the launch hot path.
    """
    with storage.open(workspace_id) as scope:
        round_ = scope.get_entity(ArenaRound, round_id)
        assert isinstance(round_, ArenaRound)
        updated_entries = []
        all_terminal = True
        for entry in round_.entries:
            status = entry.status
            if entry.experiment_id and scope.exists(Experiment, entry.experiment_id):
                experiment = scope.get_entity(Experiment, entry.experiment_id)
                assert isinstance(experiment, Experiment)
                status = str(experiment.state)
                if experiment.state not in _TERMINAL_EXPERIMENT_STATES:
                    all_terminal = False
            else:
                all_terminal = False
            updated_entries.append(
                RoundEntry(
                    variant_id=entry.variant_id,
                    experiment_id=entry.experiment_id,
                    run_job_id=entry.run_job_id,
                    status=status,
                )
            )
        round_.entries = updated_entries
        if all_terminal and round_.state == ArenaRoundState.RUNNING:
            round_.state = ArenaRoundState.COMPLETED
        scope.put_entity(round_)
    return round_


def promote_winner(
    storage: StorageInterface, *, workspace_id: str, arena_id: str, variant_id: str
) -> dict[str, object]:
    """Mark `variant_id` as the arena's winner. Never mutates git.

    Merging or opening a PR for agent-authored code without human review is
    a bad default — instead this returns copy-paste-ready commands so the
    user (or the driving coding agent) does the merge deliberately.
    """
    with storage.open(workspace_id) as scope:
        arena = scope.get_entity(Arena, arena_id)
        assert isinstance(arena, Arena)
        variant = scope.get_entity(ArenaVariant, variant_id)
        assert isinstance(variant, ArenaVariant)
        if variant.arena_id != arena_id:
            raise SelfEvalsUserError(f"variant {variant_id} does not belong to arena {arena_id}")
        arena.winner_variant_id = variant_id
        arena.state = ArenaState.COMPLETED
        scope.put_entity(arena)

    sha = variant.resolved_sha or variant.git_ref
    return {
        "winner_variant_id": variant_id,
        "git_ref": variant.git_ref,
        "resolved_sha": variant.resolved_sha,
        "suggested_commands": [
            f"git -C {arena.repo_path} merge {sha}",
            f"gh pr create --head {variant.git_ref} --title {arena.name + ': ' + variant.name!r}",
        ],
    }


def cleanup_arena(storage: StorageInterface, *, workspace_id: str, arena_id: str) -> None:
    """Remove every variant's worktree and mark the arena archived."""
    with storage.open(workspace_id) as scope:
        arena = scope.get_entity(Arena, arena_id)
        assert isinstance(arena, Arena)
        variants = [
            v
            for v in scope.list_entities(ArenaVariant, ListFilter(where={"arena_id": arena_id}))
            if isinstance(v, ArenaVariant)
        ]
        for variant in variants:
            if variant.worktree_path:
                try:
                    remove_worktree(arena.repo_path, Path(variant.worktree_path))
                except WorktreeError:
                    logger.warning("failed to remove worktree for variant %s", variant.id, exc_info=True)
            variant.state = ArenaVariantState.RETIRED
            scope.put_entity(variant)
        try:
            prune(arena.repo_path)
        except WorktreeError:
            logger.warning("git worktree prune failed for arena %s", arena_id, exc_info=True)
        arena.state = ArenaState.ARCHIVED
        scope.put_entity(arena)
