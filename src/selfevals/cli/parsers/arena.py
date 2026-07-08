"""Arena: bake off N git-branch code variants of an agent in parallel."""

from __future__ import annotations

import argparse

from selfevals.cli import arena_commands
from selfevals.cli._help import make_subparser


def add_arena(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p_arena = make_subparser(
        sub,
        "arena",
        help_text="Bake off N git-branch code variants of an agent, in parallel, on one dataset.",
        examples=[
            "selfevals arena create ws_01HZZZ... --name tts --goal '...' "
            "--repo /path/to/repo --command python agent.py "
            "--objective-metric pass_rate --spec-json @spec_template.json",
            "selfevals arena variant add ws_01HZZZ... arn_01H... "
            "--name elevenlabs --ref arena/elevenlabs",
            "selfevals arena round ws_01HZZZ... arn_01H...",
            "selfevals arena bundle ws_01HZZZ... arn_01H... | jq .leaderboard",
            "selfevals arena promote ws_01HZZZ... arn_01H... var_01H...",
        ],
    )
    arena_sub = p_arena.add_subparsers(dest="arena_command", required=True)

    p_arena_create = arena_sub.add_parser(
        "create",
        help="Create a new arena.",
        description=(
            "Create an Arena: N code variants of one agent competing on the same "
            "dataset+graders. `--spec-json` is the same JSON shape as a spec_inline "
            "experiment spec (dataset/graders/run/target), minus `agent:` — Arena "
            "injects that per variant. Accepts inline JSON or `@path/to/file.json`."
        ),
        epilog=(
            "Example:\n"
            "  selfevals arena create ws_01HZZZ... --name tts-bakeoff "
            "--goal 'cheapest TTS with acceptable latency' --repo /path/to/repo "
            "--command python agent.py --objective-metric pass_rate "
            "--spec-json @spec_template.json"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_arena_create.add_argument("workspace_id")
    p_arena_create.add_argument("--name", required=True, help="Human-readable arena name.")
    p_arena_create.add_argument("--goal", required=True, help="What this bake-off is trying to answer.")
    p_arena_create.add_argument(
        "--repo", required=True, help="Absolute path to the git repo under test."
    )
    p_arena_create.add_argument(
        "--command",
        required=True,
        nargs="+",
        help="Argv run per variant (cwd is set to that variant's worktree automatically).",
    )
    p_arena_create.add_argument(
        "--objective-metric", dest="objective_metric", required=True,
        help="Name of the primary metric variants are ranked on.",
    )
    p_arena_create.add_argument(
        "--spec-json",
        dest="spec_json",
        required=True,
        help="Spec template JSON (inline, or @path/to/file.json). Same shape as "
        "spec_inline minus `agent:`.",
    )
    p_arena_create.add_argument("--dataset-id", dest="dataset_id", default=None)
    p_arena_create.add_argument("--max-rounds", dest="max_rounds", type=int, default=None)
    p_arena_create.add_argument("--max-variants", dest="max_variants", type=int, default=None)
    p_arena_create.set_defaults(func=arena_commands.cmd_arena_create)

    p_arena_list = arena_sub.add_parser(
        "list",
        help="List arenas in a workspace.",
        description="List every arena stored in the given workspace.",
        epilog="Example:\n  selfevals arena list ws_01HZZZ...",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_arena_list.add_argument("workspace_id")
    p_arena_list.set_defaults(func=arena_commands.cmd_arena_list)

    p_arena_show = arena_sub.add_parser(
        "show",
        help="Show one arena's detail: variants and rounds.",
        description="Show an arena's state plus its variants and round history.",
        epilog="Example:\n  selfevals arena show ws_01HZZZ... arn_01H...",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_arena_show.add_argument("workspace_id")
    p_arena_show.add_argument("arena_id")
    p_arena_show.set_defaults(func=arena_commands.cmd_arena_show)

    p_arena_variant = arena_sub.add_parser(
        "variant",
        help="Register or manage arena variants.",
        description="Manage an arena's code variants.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    arena_variant_sub = p_arena_variant.add_subparsers(dest="arena_variant_command", required=True)
    p_arena_variant_add = arena_variant_sub.add_parser(
        "add",
        help="Register a new variant against an existing git ref.",
        description=(
            "Register a variant: an existing git branch/tag/commit in the arena's "
            "repo. Resolves the ref to a commit SHA immediately, then prepares a "
            "worktree + optional setup_command in the background."
        ),
        epilog=(
            "Example:\n"
            "  selfevals arena variant add ws_01HZZZ... arn_01H... "
            "--name elevenlabs --ref arena/elevenlabs --setup 'uv sync' "
            "--hypothesis 'lower TTFB than AssemblyAI'"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_arena_variant_add.add_argument("workspace_id")
    p_arena_variant_add.add_argument("arena_id")
    p_arena_variant_add.add_argument("--name", required=True, help="Friendly variant name.")
    p_arena_variant_add.add_argument(
        "--ref", required=True, help="Git branch, tag, or commit-ish to check out."
    )
    p_arena_variant_add.add_argument(
        "--setup-command",
        dest="setup_command",
        default=None,
        help="Shell-split setup command run once in the worktree (e.g. 'uv sync').",
    )
    p_arena_variant_add.add_argument(
        "--hypothesis", default=None, help="Why you expect this variant to win (for the bundle)."
    )
    p_arena_variant_add.set_defaults(func=arena_commands.cmd_arena_variant_add)

    p_arena_round = arena_sub.add_parser(
        "round",
        help="Launch a round: every ready variant runs in parallel.",
        description=(
            "Launch a round of an arena: every `ready` variant (or a chosen subset) "
            "runs as its own child experiment, in parallel, on the same dataset."
        ),
        epilog=(
            "Example:\n"
            "  selfevals arena round ws_01HZZZ... arn_01H...\n"
            "  selfevals arena round ws_01HZZZ... arn_01H... --variants var_a,var_b --reps 3"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_arena_round.add_argument("workspace_id")
    p_arena_round.add_argument("arena_id")
    p_arena_round.add_argument(
        "--variants", default=None, help="Comma-separated variant ids (default: all ready variants)."
    )
    p_arena_round.add_argument("--reps", type=int, default=1, help="Repetitions per case (default 1).")
    p_arena_round.set_defaults(func=arena_commands.cmd_arena_round)

    p_arena_estimate = arena_sub.add_parser(
        "estimate-cost",
        help="Estimate a round's cost from this arena's own run history.",
        description=(
            "Extrapolate a round's cost from past rounds in this arena: each "
            "variant's own average if it has run before, else the arena-wide "
            "average. Returns no estimate (not zero) when nothing has run yet."
        ),
        epilog="Example:\n  selfevals arena estimate-cost ws_01HZZZ... arn_01H...",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_arena_estimate.add_argument("workspace_id")
    p_arena_estimate.add_argument("arena_id")
    p_arena_estimate.add_argument(
        "--variants", default=None, help="Comma-separated variant ids (default: all ready variants)."
    )
    p_arena_estimate.add_argument("--reps", type=int, default=1, help="Repetitions per case (default 1).")
    p_arena_estimate.set_defaults(func=arena_commands.cmd_arena_estimate_cost)

    p_arena_bundle = arena_sub.add_parser(
        "bundle",
        help="Print the cross-variant bundle as JSON (leaderboard, pairwise diffs, failures).",
        description=(
            "Print the ArenaBundle as JSON on stdout: leaderboard, each variant's "
            "diff against the current best, exemplar failures, and the write-URL "
            "contract. This is what the `arena-iterate` skill reads to decide what "
            "to try next."
        ),
        epilog="Example:\n  selfevals arena bundle ws_01HZZZ... arn_01H... | jq .leaderboard",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_arena_bundle.add_argument("workspace_id")
    p_arena_bundle.add_argument("arena_id")
    p_arena_bundle.add_argument(
        "--round", type=int, default=None, help="Round index (default: most recently launched)."
    )
    p_arena_bundle.set_defaults(func=arena_commands.cmd_arena_bundle)

    p_arena_promote = arena_sub.add_parser(
        "promote",
        help="Mark a variant as the arena's winner (never touches git).",
        description=(
            "Mark `variant_id` as the arena's winner. Never merges or pushes — "
            "prints copy-paste git/PR commands for a human to run deliberately."
        ),
        epilog="Example:\n  selfevals arena promote ws_01HZZZ... arn_01H... var_01H...",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_arena_promote.add_argument("workspace_id")
    p_arena_promote.add_argument("arena_id")
    p_arena_promote.add_argument("variant_id")
    p_arena_promote.set_defaults(func=arena_commands.cmd_arena_promote)

    p_arena_prune = arena_sub.add_parser(
        "prune",
        help="Remove an arena's worktrees and archive it.",
        description="Remove every variant's git worktree and mark the arena archived.",
        epilog="Example:\n  selfevals arena prune ws_01HZZZ... arn_01H...",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_arena_prune.add_argument("workspace_id")
    p_arena_prune.add_argument("arena_id")
    p_arena_prune.set_defaults(func=arena_commands.cmd_arena_prune)

    p_arena_gc = arena_sub.add_parser(
        "gc",
        help="Sweep SELFEVALS_WORKTREES_DIR for orphaned worktrees, across all workspaces.",
        description=(
            "Remove worktree directories no live ArenaVariant references — left "
            "behind by a crash mid-registration or DB rows removed outside "
            "`cleanup_arena`. Global: scans every workspace, since the worktrees "
            "directory is shared, not per-workspace."
        ),
        epilog="Example:\n  selfevals arena gc --dry-run",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_arena_gc.add_argument(
        "--dry-run", action="store_true", help="List what would be removed without deleting."
    )
    p_arena_gc.set_defaults(func=arena_commands.cmd_arena_gc)
