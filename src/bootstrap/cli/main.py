"""CLI entry point.

`app()` is what `bootstrap` resolves to via the project script entry.
It dispatches to subcommand handlers in `bootstrap.cli.commands`.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from bootstrap._errors import BootstrapUserError
from bootstrap.cli import commands
from bootstrap.cli._help import make_subparser
from bootstrap.version import __version__


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bootstrap",
        description="Self-improving evals framework for AI agents.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  bootstrap init my-team\n"
            "  bootstrap run evals/experiments/example_pingpong.yaml --no-persist"
        ),
    )
    parser.add_argument("--version", action="version", version=f"bootstrap {__version__}")
    parser.add_argument(
        "--db",
        default="./bootstrap.sqlite",
        help="Path to SQLite database file (default: ./bootstrap.sqlite).",
    )

    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    # --- init ---
    p_init = make_subparser(
        sub,
        "init",
        help_text="Create a new workspace and seed default roles.",
        description=(
            "Create (or re-open, idempotently) a workspace identified by SLUG. "
            "Seeds default member roles for the owner."
        ),
        examples=[
            "bootstrap init my-team",
            "bootstrap init my-team --name 'My Team' --user alice",
        ],
    )
    p_init.add_argument("slug", help="Workspace slug (kebab-case).")
    p_init.add_argument("--name", help="Display name (default: slug).")
    p_init.add_argument("--user", default="local", help="Owner user id.")
    p_init.set_defaults(func=commands.cmd_init)

    # --- workspace ---
    p_ws = make_subparser(
        sub,
        "workspace",
        help_text="Inspect workspaces (show their metadata and counts).",
        examples=["bootstrap workspace show ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"],
    )
    ws_sub = p_ws.add_subparsers(dest="ws_command", required=True)
    p_ws_show = ws_sub.add_parser(
        "show",
        help="Show a workspace by id.",
        description="Print a workspace's metadata and experiment count.",
        epilog="Example:\n  bootstrap workspace show ws_01HZZZZZZZZZZZZZZZZZZZZZZZ",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_ws_show.add_argument("workspace_id")
    p_ws_show.set_defaults(func=commands.cmd_workspace_show)

    # --- experiment ---
    p_exp = make_subparser(
        sub,
        "experiment",
        help_text="List and inspect experiments inside a workspace.",
        examples=[
            "bootstrap experiment list ws_01HZZZZZZZZZZZZZZZZZZZZZZZ",
            "bootstrap experiment show ws_01HZZZ... exp_01HXXX...",
        ],
    )
    exp_sub = p_exp.add_subparsers(dest="exp_command", required=True)
    p_exp_list = exp_sub.add_parser(
        "list",
        help="List experiments in a workspace.",
        description="List every experiment stored in the given workspace.",
        epilog="Example:\n  bootstrap experiment list ws_01HZZZZZZZZZZZZZZZZZZZZZZZ",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_exp_list.add_argument("workspace_id")
    p_exp_list.set_defaults(func=commands.cmd_experiment_list)
    p_exp_show = exp_sub.add_parser(
        "show",
        help="Show one experiment.",
        description="Show one experiment's spec, target, and iteration count.",
        epilog="Example:\n  bootstrap experiment show ws_01HZZZ... exp_01HXXX...",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_exp_show.add_argument("workspace_id")
    p_exp_show.add_argument("experiment_id")
    p_exp_show.set_defaults(func=commands.cmd_experiment_show)

    # --- iteration ---
    p_iter = make_subparser(
        sub,
        "iteration",
        help_text="List iterations recorded for an experiment.",
        examples=["bootstrap iteration list ws_01HZZZ... exp_01HXXX..."],
    )
    iter_sub = p_iter.add_subparsers(dest="iter_command", required=True)
    p_iter_list = iter_sub.add_parser(
        "list",
        help="List iterations for an experiment.",
        description=(
            "List the iterations stored for an experiment, "
            "with their primary metric and decision outcome."
        ),
        epilog="Example:\n  bootstrap iteration list ws_01HZZZ... exp_01HXXX...",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_iter_list.add_argument("workspace_id")
    p_iter_list.add_argument("experiment_id")
    p_iter_list.set_defaults(func=commands.cmd_iteration_list)

    # --- report ---
    p_report = make_subparser(
        sub,
        "report",
        help_text="Render a markdown or JSON report from stored iterations.",
        description=(
            "Render a report for an experiment using already-persisted "
            "iterations. Markdown by default; JSON via --format."
        ),
        examples=[
            "bootstrap report ws_01HZZZ... exp_01HXXX...",
            "bootstrap report ws_01HZZZ... exp_01HXXX... --format json",
        ],
    )
    p_report.add_argument("workspace_id")
    p_report.add_argument("experiment_id")
    p_report.add_argument("--format", choices=["markdown", "json"], default="markdown")
    p_report.set_defaults(func=commands.cmd_report)

    # --- run ---
    p_run = make_subparser(
        sub,
        "run",
        help_text="Run an experiment spec end-to-end (YAML).",
        description=(
            "Load a YAML experiment spec, resolve its agent entrypoint, "
            "run every case through the configured proposer/grader, "
            "persist iterations to SQLite (unless --no-persist), and "
            "print a report."
        ),
        examples=[
            "bootstrap run evals/experiments/example_pingpong.yaml --no-persist",
            "bootstrap run evals/experiments/example_pingpong.yaml --reps 3 --format json",
        ],
    )
    p_run.add_argument("spec", help="Path to evals/experiments/<name>.yaml")
    p_run.add_argument(
        "--workspace",
        help="Workspace id override (otherwise read from the spec's `workspace:` key).",
    )
    p_run.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Override experiment.run.max_iterations for this run.",
    )
    p_run.add_argument(
        "--reps",
        type=int,
        default=1,
        help="Repetitions per case (default 1).",
    )
    p_run.add_argument(
        "--format",
        choices=["markdown", "json"],
        default="markdown",
        help="Report format printed at the end of the run.",
    )
    p_run.add_argument(
        "--no-persist",
        action="store_true",
        help="Do not write iterations/decisions to the SQLite db.",
    )
    p_run.set_defaults(func=commands.cmd_run)

    # --- compare ---
    p_compare = make_subparser(
        sub,
        "compare",
        help_text="Diff two iterations side-by-side (by primary metric).",
        description=(
            "Print the primary metric for two iterations of the same "
            "experiment, plus their delta and decision outcomes."
        ),
        examples=["bootstrap compare ws_01HZZZ... iter_01HAAA... iter_01HBBB..."],
    )
    p_compare.add_argument("workspace_id")
    p_compare.add_argument("iter_a_id")
    p_compare.add_argument("iter_b_id")
    p_compare.set_defaults(func=commands.cmd_compare)

    # --- estimate ---
    p_estimate = make_subparser(
        sub,
        "estimate",
        help_text="Dry-run cost estimate for a search space x cases x reps.",
        description=(
            "Compute upper-bound agent calls and USD cost for a "
            "hypothetical run, without touching the db or any agent."
        ),
        examples=[
            "bootstrap estimate --cases 50 --space-size 8 --reps 3 --cost-per-call 0.01",
        ],
    )
    p_estimate.add_argument("--cases", type=int, required=True, help="Number of evaluation cases.")
    p_estimate.add_argument(
        "--space-size", type=int, required=True, help="Number of proposals in the search space."
    )
    p_estimate.add_argument("--reps", type=int, default=1, help="Repetitions per case (default 1).")
    p_estimate.add_argument(
        "--cost-per-call", type=float, required=True, help="Estimated USD per agent call."
    )
    p_estimate.set_defaults(func=commands.cmd_estimate)

    return parser


def app(argv: Sequence[str] | None = None) -> int:
    """Programmatic entry point. Returns the intended process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except BootstrapUserError as exc:
        # User-correctable errors get a clean one-line message (no
        # traceback) and exit code 2 — the standard "user input was bad"
        # convention. Internal errors (anything else) keep their
        # traceback and become exit 1 via the normal exception bubbling.
        print(f"error: {exc}", file=sys.stderr)
        return 2


def main() -> None:  # pragma: no cover - thin wrapper for the console script.
    raise SystemExit(app())


if __name__ == "__main__":  # pragma: no cover
    main()
