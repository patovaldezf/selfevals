"""CLI entry point.

`app()` is what `bootstrap` resolves to via the project script entry.
It dispatches to subcommand handlers in `bootstrap.cli.commands`.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from bootstrap.cli import commands
from bootstrap.version import __version__


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bootstrap",
        description="Self-improving evals framework for AI agents.",
    )
    parser.add_argument("--version", action="version", version=f"bootstrap {__version__}")
    parser.add_argument(
        "--db",
        default="./bootstrap.sqlite",
        help="Path to SQLite database file (default: ./bootstrap.sqlite).",
    )

    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    p_init = sub.add_parser("init", help="Initialize a new workspace.")
    p_init.add_argument("slug", help="Workspace slug (kebab-case).")
    p_init.add_argument("--name", help="Display name (default: slug).")
    p_init.add_argument("--user", default="local", help="Owner user id.")
    p_init.set_defaults(func=commands.cmd_init)

    p_ws = sub.add_parser("workspace", help="Workspace operations.")
    ws_sub = p_ws.add_subparsers(dest="ws_command", required=True)
    p_ws_show = ws_sub.add_parser("show", help="Show a workspace.")
    p_ws_show.add_argument("workspace_id")
    p_ws_show.set_defaults(func=commands.cmd_workspace_show)

    p_exp = sub.add_parser("experiment", help="Experiment operations.")
    exp_sub = p_exp.add_subparsers(dest="exp_command", required=True)
    p_exp_list = exp_sub.add_parser("list", help="List experiments in a workspace.")
    p_exp_list.add_argument("workspace_id")
    p_exp_list.set_defaults(func=commands.cmd_experiment_list)
    p_exp_show = exp_sub.add_parser("show", help="Show one experiment.")
    p_exp_show.add_argument("workspace_id")
    p_exp_show.add_argument("experiment_id")
    p_exp_show.set_defaults(func=commands.cmd_experiment_show)

    p_iter = sub.add_parser("iteration", help="Iteration operations.")
    iter_sub = p_iter.add_subparsers(dest="iter_command", required=True)
    p_iter_list = iter_sub.add_parser("list", help="List iterations for an experiment.")
    p_iter_list.add_argument("workspace_id")
    p_iter_list.add_argument("experiment_id")
    p_iter_list.set_defaults(func=commands.cmd_iteration_list)

    p_report = sub.add_parser(
        "report", help="Render a markdown/JSON report from stored iterations."
    )
    p_report.add_argument("workspace_id")
    p_report.add_argument("experiment_id")
    p_report.add_argument(
        "--format", choices=["markdown", "json"], default="markdown"
    )
    p_report.set_defaults(func=commands.cmd_report)

    p_run = sub.add_parser(
        "run", help="Run an experiment spec end-to-end (YAML)."
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

    p_compare = sub.add_parser(
        "compare", help="Diff two iterations side-by-side (by primary metric)."
    )
    p_compare.add_argument("workspace_id")
    p_compare.add_argument("iter_a_id")
    p_compare.add_argument("iter_b_id")
    p_compare.set_defaults(func=commands.cmd_compare)

    p_estimate = sub.add_parser(
        "estimate", help="Dry-run cost estimate for a search space x cases x reps."
    )
    p_estimate.add_argument(
        "--cases", type=int, required=True, help="Number of evaluation cases."
    )
    p_estimate.add_argument(
        "--space-size", type=int, required=True, help="Number of proposals in the search space."
    )
    p_estimate.add_argument(
        "--reps", type=int, default=1, help="Repetitions per case (default 1)."
    )
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
    except commands.CommandError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def main() -> None:  # pragma: no cover - thin wrapper for the console script.
    raise SystemExit(app())


if __name__ == "__main__":  # pragma: no cover
    main()
