"""CLI entry point.

`app()` is what `selfevals` resolves to via the project script entry.
It dispatches to subcommand handlers in `selfevals.cli.commands`.

Subcommand parsers are split by concern under `cli.parsers.*`; this module
only builds the top-level parser and wires the `--version`/`--db` flags.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from selfevals._errors import SelfEvalsUserError
from selfevals.cli.parsers import analysis, arena, datasets, experiments, meta, ops
from selfevals.version import __version__


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="selfevals",
        description="Self-improving evals framework for AI agents.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  docker compose up -d          # Postgres + Redis + a worker\n"
            "  selfevals init my-team\n"
            "  selfevals run evals/experiments/example_pingpong.yaml"
        ),
    )
    parser.add_argument("--version", action="version", version=f"selfevals {__version__}")
    parser.add_argument(
        "--db",
        default=None,
        help="Postgres storage URL. Defaults to SELFEVALS_STORAGE_URL.",
    )

    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    meta.add_init(sub)
    meta.add_demo(sub)
    meta.add_migrate(sub)
    experiments.add_workspace(sub)
    experiments.add_experiment(sub)
    experiments.add_iteration(sub)
    datasets.add_baseline(sub)
    datasets.add_regression(sub)
    datasets.add_dataset(sub)
    arena.add_arena(sub)
    meta.add_report(sub)
    meta.add_run(sub)
    meta.add_compare(sub)
    meta.add_estimate(sub)
    analysis.add_analyze(sub)
    analysis.add_failuremode(sub)
    ops.add_skills(sub)
    ops.add_examples(sub)
    ops.add_serve(sub)
    ops.add_worker(sub)

    return parser


def _maybe_autosync_skills() -> None:
    """Install the consumer skills into the project's .claude/skills on first use.

    A wheel can't run a post-install hook, so "skills show up when you install
    the framework" is implemented as a lazy sync on any CLI invocation: if we're
    sitting in a project (has a .claude/, pyproject.toml, or .git) we copy the
    consumer skills in. It's idempotent by content, so after the first time it
    writes nothing. Best-effort and non-fatal — a read-only FS or any error must
    never break the command the user actually ran — and opt-out via
    SELFEVALS_NO_SKILL_SYNC=1 (e.g. for CI that doesn't want surprise files).
    """
    if os.environ.get("SELFEVALS_NO_SKILL_SYNC"):
        return
    cwd = Path.cwd()
    in_project = any((cwd / marker).exists() for marker in (".claude", "pyproject.toml", ".git"))
    if not in_project:
        return
    try:
        from selfevals import skills

        skills.sync_skills()
    except (OSError, RuntimeError, KeyError):
        # Never let skill-sync failure surface to the user; it's a convenience,
        # not part of the command they asked for.
        pass


def app(argv: Sequence[str] | None = None) -> int:
    """Programmatic entry point. Returns the intended process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    _maybe_autosync_skills()
    try:
        return int(args.func(args))
    except SelfEvalsUserError as exc:
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
