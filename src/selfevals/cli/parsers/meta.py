"""Top-level lifecycle commands: init, demo, migrate-sqlite, report, run, compare, estimate."""

from __future__ import annotations

import argparse

from selfevals.cli import commands, demo_commands, migrate_commands
from selfevals.cli._help import make_subparser


def add_init(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p_init = make_subparser(
        sub,
        "init",
        help_text="Create a new workspace and seed default roles.",
        description=(
            "Create (or re-open, idempotently) a workspace identified by SLUG. "
            "Seeds default member roles for the owner."
        ),
        examples=[
            "selfevals init my-team",
            "selfevals init my-team --name 'My Team' --user alice",
        ],
    )
    p_init.add_argument("slug", help="Workspace slug (kebab-case).")
    p_init.add_argument("--name", help="Display name (default: slug).")
    p_init.add_argument("--user", default="local", help="Owner user id.")
    p_init.set_defaults(func=commands.cmd_init)


def add_demo(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p_demo = make_subparser(
        sub,
        "demo",
        help_text="Seed a real, end-to-end demo workspace (real LLM calls).",
        description=(
            "One command that seeds the `demo` workspace, runs two real example "
            "experiments through the Anthropic-backed agent (single-shot + "
            "multi-turn), runs a real pairwise tournament, and ingests human "
            "verdicts — so `selfevals serve` shows every web view with real data. "
            "Calls the real LLM when ANTHROPIC_API_KEY is set; falls back to a "
            "deterministic fake otherwise. Run from the repo root (examples/ must "
            "be importable). Idempotent on the workspace slug."
        ),
        examples=[
            "selfevals demo",
            "selfevals demo --slug demo --fresh",
        ],
    )
    p_demo.add_argument("--slug", default="demo", help="Workspace slug (default: demo).")
    p_demo.add_argument("--name", help="Display name (default: slug).")
    p_demo.add_argument("--user", default="local", help="Owner user id.")
    p_demo.add_argument(
        "--fresh",
        action="store_true",
        help="Signal intent to start clean (point --db at a fresh file).",
    )
    p_demo.set_defaults(func=demo_commands.cmd_demo)


def add_migrate(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p_migrate = make_subparser(
        sub,
        "migrate-sqlite",
        help_text="One-shot import of a legacy SQLite database into Postgres.",
        examples=[
            "selfevals migrate-sqlite ./selfevals.sqlite --to "
            "postgresql://selfevals:selfevals@localhost:5433/selfevals",
            "selfevals migrate-sqlite ./selfevals.sqlite --to $SELFEVALS_STORAGE_URL --dry-run",
        ],
    )
    p_migrate.add_argument("source", help="Path to the legacy SQLite file to read (read-only).")
    p_migrate.add_argument(
        "--to",
        required=True,
        help="Target Postgres storage URL (postgresql://...).",
    )
    p_migrate.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and report counts without writing anything.",
    )
    p_migrate.set_defaults(func=migrate_commands.cmd_migrate_sqlite)


def add_report(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p_report = make_subparser(
        sub,
        "report",
        help_text="Render a markdown or JSON report from stored iterations.",
        description=(
            "Render a report for an experiment using already-persisted "
            "iterations. Markdown by default; JSON via --format."
        ),
        examples=[
            "selfevals report ws_01HZZZ... exp_01HXXX...",
            "selfevals report ws_01HZZZ... exp_01HXXX... --format json",
        ],
    )
    p_report.add_argument("workspace_id")
    p_report.add_argument("experiment_id")
    p_report.add_argument("--format", choices=["markdown", "json"], default="markdown")
    p_report.set_defaults(func=commands.cmd_report)


def add_run(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p_run = make_subparser(
        sub,
        "run",
        help_text="Run an experiment spec end-to-end (YAML).",
        description=(
            "Load a YAML experiment spec, enqueue it as a sharded run, and wait "
            "for a worker to drive it to completion before printing the report. "
            "Needs Redis + a worker (locally: `docker compose up -d`)."
        ),
        examples=[
            "selfevals run evals/experiments/example_pingpong.yaml",
            "selfevals run evals/experiments/example_pingpong.yaml --reps 3 --format json",
        ],
    )
    p_run.add_argument("spec", help="Path to evals/experiments/<name>.yaml")
    p_run.add_argument(
        "--workspace",
        help="Workspace id override (otherwise read from the spec's `workspace:` key).",
    )
    p_run.add_argument(
        "--dataset",
        default=None,
        help=(
            "Run against this persisted dataset id instead of the spec's `dataset:` "
            "block (resolves its cases + split from storage). Needs persistence."
        ),
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
        "--timeout",
        type=float,
        default=600.0,
        help="Max seconds to wait for the run to finish (default 600).",
    )
    p_run.add_argument(
        "--persist-traces",
        choices=["none", "all", "failed"],
        default=None,
        help=(
            "Override run.persist_traces: which traces to store — none, all, or "
            "failed (default in the spec). Failed traces feed `analyze pull`."
        ),
    )
    p_run.set_defaults(func=commands.cmd_run)


def add_compare(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p_compare = make_subparser(
        sub,
        "compare",
        help_text="Diff two iterations side-by-side (by primary metric).",
        description=(
            "Print the primary metric for two iterations of the same "
            "experiment, plus their delta and decision outcomes."
        ),
        examples=["selfevals compare ws_01HZZZ... iter_01HAAA... iter_01HBBB..."],
    )
    p_compare.add_argument("workspace_id")
    p_compare.add_argument("iter_a_id")
    p_compare.add_argument("iter_b_id")
    p_compare.set_defaults(func=commands.cmd_compare)


def add_estimate(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p_estimate = make_subparser(
        sub,
        "estimate",
        help_text="Dry-run cost estimate for a search space x cases x reps.",
        description=(
            "Compute upper-bound agent calls and USD cost for a "
            "hypothetical run, without touching the db or any agent."
        ),
        examples=[
            "selfevals estimate --cases 50 --space-size 8 --reps 3 --cost-per-call 0.01",
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
