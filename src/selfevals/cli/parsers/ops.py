"""Skills, examples, serve, and worker (operational) commands."""

from __future__ import annotations

import argparse
import os

from selfevals.cli import ops_commands
from selfevals.cli._help import make_subparser


def add_skills(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p_skills = make_subparser(
        sub,
        "skills",
        help_text="List the agent skills bundled with this install, or print one's path.",
        description=(
            "selfevals ships agent skills (e.g. error-analysis) inside the "
            "package. `list` shows them; `path` prints a skill's directory so "
            "an agent or onboarding flow can read or install it."
        ),
        examples=[
            "selfevals skills list",
            "selfevals skills path error-analysis",
        ],
    )
    skills_sub = p_skills.add_subparsers(dest="skills_command", required=True)
    p_skills_list = skills_sub.add_parser(
        "list",
        help="List bundled skills.",
        description="List every agent skill shipped with this selfevals install.",
        epilog="Example:\n  selfevals skills list",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_skills_list.set_defaults(func=ops_commands.cmd_skills_list)
    p_skills_path = skills_sub.add_parser(
        "path",
        help="Print the directory of a bundled skill.",
        description="Print the on-disk directory of the named bundled skill.",
        epilog="Example:\n  selfevals skills path error-analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_skills_path.add_argument("name", help="Skill name, e.g. error-analysis.")
    p_skills_path.set_defaults(func=ops_commands.cmd_skills_path)
    p_skills_sync = skills_sub.add_parser(
        "sync",
        help="Install the bundled consumer skills into a project skill directory.",
        description=(
            "Copy the bundled skills into a project's skill directory (default "
            ".claude/skills). Installs only the consumer skills — how to use the "
            "framework — and leaves the selfevals-*-change repo-maintenance skills "
            "out unless --all is given. Idempotent: re-running writes nothing when "
            "everything is already current. This also runs automatically on any "
            "selfevals invocation; set SELFEVALS_NO_SKILL_SYNC=1 to disable that."
        ),
        epilog="Example:\n  selfevals skills sync\n  selfevals skills sync --to .claude/skills --all",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_skills_sync.add_argument(
        "--to",
        default=None,
        help="Destination skill directory (default .claude/skills).",
    )
    p_skills_sync.add_argument(
        "--all",
        action="store_true",
        help="Also install the selfevals-*-change repo-maintenance skills.",
    )
    p_skills_sync.set_defaults(func=ops_commands.cmd_skills_sync)


def add_examples(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p_examples = make_subparser(
        sub,
        "examples",
        help_text="Copy runnable example specs into the current project.",
        examples=[
            "selfevals examples copy pingpong",
            "selfevals run evals/experiments/example_pingpong.yaml",
        ],
    )
    examples_sub = p_examples.add_subparsers(dest="examples_command", required=True)
    p_examples_copy = examples_sub.add_parser(
        "copy",
        help="Copy a runnable example by name.",
        description="Copy a packaged example spec and dataset into --to (default: cwd).",
        epilog="Example:\n  selfevals examples copy pingpong",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_examples_copy.add_argument("name", choices=sorted(ops_commands._EXAMPLE_NAMES))
    p_examples_copy.add_argument("--to", default=".", help="Destination directory (default: cwd).")
    p_examples_copy.set_defaults(func=ops_commands.cmd_examples_copy)


def add_serve(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p_serve = make_subparser(
        sub,
        "serve",
        help_text="Run the web UI + API in one process (no manual proxy).",
        description=(
            "Start the FastAPI bridge (and optionally the SvelteKit UI built "
            "by `npm run build`) so a dev can see iterations, traces, and "
            "live runs without juggling two terminals. Without --web-dist "
            "the API runs alone — useful for headless usage or when the "
            "web is served from `npm run dev` separately."
        ),
        examples=[
            "selfevals --db postgresql://localhost:5433/selfevals serve",
            "selfevals serve --web-dist web/build --port 8080",
            "selfevals serve --no-web",
        ],
    )
    p_serve.add_argument("--host", default="127.0.0.1", help="Bind host (default 127.0.0.1).")
    p_serve.add_argument("--port", type=int, default=8000, help="Bind port (default 8000).")
    p_serve.add_argument(
        "--web-dist",
        default=None,
        help=(
            "Path to a `npm run build` output (adapter-node) for the web UI. "
            "If present, mounts the SPA at `/` and serves its assets; the "
            "API stays at `/api`. If omitted, only the API is served."
        ),
    )
    p_serve.add_argument(
        "--no-web",
        action="store_true",
        help="Explicitly disable the web UI even if a web build is auto-detected.",
    )
    p_serve.add_argument(
        "--reload",
        action="store_true",
        help="Enable uvicorn auto-reload (dev only).",
    )
    p_serve.set_defaults(func=ops_commands.cmd_serve)


def add_worker(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p_worker = make_subparser(
        sub,
        "worker",
        help_text="Run durable background workers.",
        examples=[
            # Must be the SAME Redis DB the CLI/API enqueue to (see .env.example).
            "SELFEVALS_REDIS_URL=redis://localhost:6380/15 selfevals worker runs",
            "selfevals worker runs --once",
        ],
    )
    worker_sub = p_worker.add_subparsers(dest="worker_command", required=True)
    p_worker_runs = worker_sub.add_parser(
        "runs",
        help="Consume durable experiment run jobs from Redis Streams.",
        description="Run the worker that replaces API daemon threads for launched experiments.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_worker_runs.add_argument(
        "--redis-url",
        default=os.environ.get("SELFEVALS_REDIS_URL"),
        help="Redis URL for the run job stream (default: SELFEVALS_REDIS_URL).",
    )
    p_worker_runs.add_argument("--consumer", default=None, help="Worker consumer name.")
    p_worker_runs.add_argument(
        "--once",
        action="store_true",
        help="Process at most one available job and exit.",
    )
    p_worker_runs.set_defaults(func=ops_commands.cmd_worker_runs)

    p_worker_sweeper = worker_sub.add_parser(
        "sweeper",
        help="Reap run jobs whose lease lapsed (worker crash recovery).",
        description=(
            "Long-lived sweeper that fails/re-queues run jobs stranded in "
            "'running' by a worker that died without writing a terminal state "
            "(OOMKill/SIGKILL). One sweeper suffices for the whole cluster."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_worker_sweeper.add_argument(
        "--redis-url",
        default=os.environ.get("SELFEVALS_REDIS_URL"),
        help="Redis URL to re-enqueue retryable jobs (default: SELFEVALS_REDIS_URL). "
        "Omit to mark expired jobs failed without re-queuing.",
    )
    p_worker_sweeper.add_argument(
        "--interval",
        type=float,
        default=30.0,
        help="Seconds between sweeps (default: 30).",
    )
    p_worker_sweeper.add_argument(
        "--once",
        action="store_true",
        help="Sweep one batch and exit.",
    )
    p_worker_sweeper.set_defaults(func=ops_commands.cmd_worker_sweeper)
