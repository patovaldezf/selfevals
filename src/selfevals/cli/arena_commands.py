"""CLI handlers for `selfevals arena` — code-variant bake-offs over git worktrees.

Thin wrappers around `arena.service` / `arena.bundle` — the same functions the
HTTP API calls (`api.arena_ops`). `create`/`variant add`/`round` accept the
spec_template/agent_command JSON inline or from a file, same as `--spec-json`
elsewhere in this CLI; `bundle` prints the ArenaBundle as JSON on stdout so a
coding agent (or the `arena-iterate` skill) can pipe it straight into `jq`
without going through HTTP.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from selfevals._errors import SelfEvalsUserError
from selfevals.arena import service as arena_service
from selfevals.arena.bundle import build_bundle
from selfevals.cli.commands import CommandError, _require_entity, _storage
from selfevals.runner.launch import ensure_workspace_by_id
from selfevals.schemas.arena import Arena, ArenaRound, ArenaVariant
from selfevals.schemas.enums import ArenaVariantState
from selfevals.storage.factory import resolve_storage_url
from selfevals.storage.interface import ListFilter


def _read_json_arg(value: str) -> dict[str, object]:
    """Accept either inline JSON or `@path/to/file.json`."""
    raw = Path(value[1:]).read_text() if value.startswith("@") else value
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CommandError(f"invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise CommandError("expected a JSON object")
    return parsed


def cmd_arena_create(args: argparse.Namespace) -> int:
    spec_template = _read_json_arg(args.spec_json)
    budget: dict[str, object] | None = None
    if args.max_rounds is not None or args.max_variants is not None:
        budget = {}
        if args.max_rounds is not None:
            budget["max_rounds"] = args.max_rounds
        if args.max_variants is not None:
            budget["max_variants"] = args.max_variants

    storage = _storage(args)
    try:
        ensure_workspace_by_id(storage, args.workspace_id)
        arena = arena_service.create_arena(
            storage,
            workspace_id=args.workspace_id,
            req=arena_service.CreateArenaRequest(
                name=args.name,
                goal=args.goal,
                repo_path=args.repo,
                agent_command=args.command,
                spec_template=spec_template,
                objective_metric=args.objective_metric,
                dataset_id=args.dataset_id,
                budget=budget,
            ),
        )
    except SelfEvalsUserError as exc:
        raise CommandError(str(exc)) from exc
    finally:
        storage.close()

    print(f"created arena id={arena.id}")
    print(f"  name:   {arena.name}")
    print(f"  state:  {arena.state}")
    return 0


def cmd_arena_list(args: argparse.Namespace) -> int:
    storage = _storage(args)
    try:
        with storage.open(args.workspace_id) as scope:
            arenas = [
                a for a in scope.list_entities(Arena, ListFilter()) if isinstance(a, Arena)
            ]
    finally:
        storage.close()

    if not arenas:
        print("(no arenas)")
        return 0
    for arena in arenas:
        print(
            f"{arena.id}  state={arena.state}  round={arena.current_round}  "
            f"winner={arena.winner_variant_id or '-'}  name={arena.name}"
        )
    return 0


def cmd_arena_show(args: argparse.Namespace) -> int:
    storage = _storage(args)
    try:
        with storage.open(args.workspace_id) as scope:
            arena = _require_entity(scope, Arena, args.arena_id)
            assert isinstance(arena, Arena)
            variants = [
                v
                for v in scope.list_entities(ArenaVariant, ListFilter(where={"arena_id": arena.id}))
                if isinstance(v, ArenaVariant)
            ]
            round_ids = [
                r.id
                for r in scope.list_entities(
                    ArenaRound, ListFilter(where={"arena_id": arena.id}, order_by="index", order_desc=False)
                )
                if isinstance(r, ArenaRound)
            ]
        # Sync each round's entries against its child experiments' current
        # state — list_entities alone returns whatever launch_round wrote at
        # dispatch time, which never advances past "running" on its own.
        rounds = [
            arena_service.refresh_round_status(storage, workspace_id=args.workspace_id, round_id=rid)
            for rid in round_ids
        ]
        rounds.sort(key=lambda r: r.index)
    finally:
        storage.close()

    print(f"arena id={arena.id}")
    print(f"  name:      {arena.name}")
    print(f"  goal:      {arena.goal}")
    print(f"  repo:      {arena.repo_path}")
    print(f"  state:     {arena.state}")
    print(f"  round:     {arena.current_round}")
    print(f"  winner:    {arena.winner_variant_id or '-'}")
    print(f"  variants ({len(variants)}):")
    for v in variants:
        print(f"    {v.id}  state={v.state}  ref={v.git_ref}  name={v.name}")
    print(f"  rounds ({len(rounds)}):")
    for r in rounds:
        print(f"    round={r.index}  state={r.state}  entries={len(r.entries)}")
    return 0


def cmd_arena_variant_add(args: argparse.Namespace) -> int:
    setup_command = args.setup_command.split() if args.setup_command else None
    storage = _storage(args)
    try:
        variant = arena_service.register_variant(
            storage,
            storage_url=resolve_storage_url(args.db),
            workspace_id=args.workspace_id,
            arena_id=args.arena_id,
            req=arena_service.RegisterVariantRequest(
                name=args.name,
                git_ref=args.ref,
                setup_command=setup_command,
                hypothesis=args.hypothesis,
            ),
        )
    except SelfEvalsUserError as exc:
        raise CommandError(str(exc)) from exc
    finally:
        storage.close()

    print(f"registered variant id={variant.id} (state={variant.state})")
    print(f"  resolved_sha: {variant.resolved_sha}")
    print("preparing in the background — check `arena show` until state=ready")
    return 0


def cmd_arena_round(args: argparse.Namespace) -> int:
    variant_ids = args.variants.split(",") if args.variants else None
    storage = _storage(args)
    try:
        round_ = arena_service.launch_round(
            storage,
            storage_url=resolve_storage_url(args.db),
            workspace_id=args.workspace_id,
            arena_id=args.arena_id,
            req=arena_service.LaunchRoundRequest(variant_ids=variant_ids, reps=args.reps),
        )
    except SelfEvalsUserError as exc:
        raise CommandError(str(exc)) from exc
    finally:
        storage.close()

    print(f"launched round {round_.index} id={round_.id} (state={round_.state})")
    for entry in round_.entries:
        print(f"  variant={entry.variant_id}  experiment={entry.experiment_id}")
    return 0


def cmd_arena_estimate_cost(args: argparse.Namespace) -> int:
    variant_ids = args.variants.split(",") if args.variants else None
    storage = _storage(args)
    try:
        with storage.open(args.workspace_id) as scope:
            arena = _require_entity(scope, Arena, args.arena_id)
            assert isinstance(arena, Arena)
            target_ids = variant_ids
            if target_ids is None:
                target_ids = [
                    v.id
                    for v in scope.list_entities(ArenaVariant, ListFilter(where={"arena_id": args.arena_id}))
                    if isinstance(v, ArenaVariant) and v.state == ArenaVariantState.READY
                ]
        estimate = arena_service.estimate_round_cost(
            storage,
            workspace_id=args.workspace_id,
            arena_id=args.arena_id,
            variant_ids=target_ids,
            reps=args.reps,
        )
    finally:
        storage.close()

    if estimate.estimated_usd is None:
        print(f"no estimate available: {estimate.basis}")
    else:
        print(f"estimated cost: ${estimate.estimated_usd:.4f}  ({estimate.basis})")
    return 0


def cmd_arena_bundle(args: argparse.Namespace) -> int:
    storage = _storage(args)
    try:
        bundle = build_bundle(
            storage, workspace_id=args.workspace_id, arena_id=args.arena_id, round_index=args.round
        )
    finally:
        storage.close()
    print(json.dumps(bundle.model_dump(mode="json"), indent=2))
    return 0


def cmd_arena_promote(args: argparse.Namespace) -> int:
    storage = _storage(args)
    try:
        result = arena_service.promote_winner(
            storage, workspace_id=args.workspace_id, arena_id=args.arena_id, variant_id=args.variant_id
        )
    except SelfEvalsUserError as exc:
        raise CommandError(str(exc)) from exc
    finally:
        storage.close()

    commands = result["suggested_commands"]
    assert isinstance(commands, list)
    print(f"promoted variant {result['winner_variant_id']} as the arena winner")
    print("suggested commands:")
    for cmd in commands:
        print(f"  {cmd}")
    return 0


def cmd_arena_prune(args: argparse.Namespace) -> int:
    storage = _storage(args)
    try:
        arena_service.cleanup_arena(storage, workspace_id=args.workspace_id, arena_id=args.arena_id)
    finally:
        storage.close()
    print(f"cleaned up worktrees for arena {args.arena_id} (state=archived)")
    return 0


def cmd_arena_gc(args: argparse.Namespace) -> int:
    """Sweep `SELFEVALS_WORKTREES_DIR` for directories no live variant owns.

    Global — not scoped to one workspace, since worktrees_root() is shared
    across all of them. Safe to run any time; a worktree still owned by a
    variant is never touched, only directories the DB has no record of.
    """
    storage = _storage(args)
    try:
        orphans = arena_service.gc_orphaned_worktrees(storage, dry_run=args.dry_run)
    finally:
        storage.close()

    if not orphans:
        print("no orphaned worktrees found")
        return 0
    verb = "would remove" if args.dry_run else "removed"
    print(f"{verb} {len(orphans)} orphaned worktree(s):")
    for orphan in orphans:
        print(f"  {orphan.path}  ({orphan.reason})")
    return 0
