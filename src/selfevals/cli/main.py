"""CLI entry point.

`app()` is what `selfevals` resolves to via the project script entry.
It dispatches to subcommand handlers in `selfevals.cli.commands`.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from selfevals._errors import SelfEvalsUserError
from selfevals.cli import analyze_commands, commands
from selfevals.cli._help import make_subparser
from selfevals.version import __version__


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="selfevals",
        description="Self-improving evals framework for AI agents.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  selfevals init my-team\n"
            "  selfevals run evals/experiments/example_pingpong.yaml --no-persist"
        ),
    )
    parser.add_argument("--version", action="version", version=f"selfevals {__version__}")
    parser.add_argument(
        "--db",
        default="./selfevals.sqlite",
        help="Path to SQLite database file (default: ./selfevals.sqlite).",
    )

    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")
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
    p_ws = make_subparser(
        sub,
        "workspace",
        help_text="Inspect workspaces (show their metadata and counts).",
        examples=["selfevals workspace show ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"],
    )
    ws_sub = p_ws.add_subparsers(dest="ws_command", required=True)
    p_ws_show = ws_sub.add_parser(
        "show",
        help="Show a workspace by id.",
        description="Print a workspace's metadata and experiment count.",
        epilog="Example:\n  selfevals workspace show ws_01HZZZZZZZZZZZZZZZZZZZZZZZ",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_ws_show.add_argument("workspace_id")
    p_ws_show.set_defaults(func=commands.cmd_workspace_show)
    p_exp = make_subparser(
        sub,
        "experiment",
        help_text="List and inspect experiments inside a workspace.",
        examples=[
            "selfevals experiment list ws_01HZZZZZZZZZZZZZZZZZZZZZZZ",
            "selfevals experiment show ws_01HZZZ... exp_01HXXX...",
        ],
    )
    exp_sub = p_exp.add_subparsers(dest="exp_command", required=True)
    p_exp_list = exp_sub.add_parser(
        "list",
        help="List experiments in a workspace.",
        description="List every experiment stored in the given workspace.",
        epilog="Example:\n  selfevals experiment list ws_01HZZZZZZZZZZZZZZZZZZZZZZZ",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_exp_list.add_argument("workspace_id")
    p_exp_list.set_defaults(func=commands.cmd_experiment_list)
    p_exp_show = exp_sub.add_parser(
        "show",
        help="Show one experiment.",
        description="Show one experiment's spec, target, and iteration count.",
        epilog="Example:\n  selfevals experiment show ws_01HZZZ... exp_01HXXX...",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_exp_show.add_argument("workspace_id")
    p_exp_show.add_argument("experiment_id")
    p_exp_show.set_defaults(func=commands.cmd_experiment_show)
    p_iter = make_subparser(
        sub,
        "iteration",
        help_text="List iterations recorded for an experiment.",
        examples=["selfevals iteration list ws_01HZZZ... exp_01HXXX..."],
    )
    iter_sub = p_iter.add_subparsers(dest="iter_command", required=True)
    p_iter_list = iter_sub.add_parser(
        "list",
        help="List iterations for an experiment.",
        description=(
            "List the iterations stored for an experiment, "
            "with their primary metric and decision outcome."
        ),
        epilog="Example:\n  selfevals iteration list ws_01HZZZ... exp_01HXXX...",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_iter_list.add_argument("workspace_id")
    p_iter_list.add_argument("experiment_id")
    p_iter_list.set_defaults(func=commands.cmd_iteration_list)
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
            "selfevals run evals/experiments/example_pingpong.yaml --no-persist",
            "selfevals run evals/experiments/example_pingpong.yaml --reps 3 --format json",
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
    p_analyze = make_subparser(
        sub,
        "analyze",
        help_text="Error-analysis handshake: emit a bundle / ingest a result.",
        examples=[
            "selfevals analyze pull ws_01HZZZ... exp_01HXXX... > bundle.json",
            "selfevals analyze push ws_01HZZZ... exp_01HXXX... < result.json",
        ],
    )
    analyze_sub = p_analyze.add_subparsers(dest="analyze_command", required=True)
    p_an_pull = analyze_sub.add_parser(
        "pull",
        help="Emit an AnalysisBundle (failed traces + live taxonomy) as JSON.",
        description=(
            "Gather an experiment's failed traces and the live failure-mode "
            "taxonomy into a JSON bundle on stdout, for an external coding "
            "agent to do open/axial coding against."
        ),
        epilog="Example:\n  selfevals analyze pull ws_01HZZZ... exp_01HXXX... > bundle.json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_an_pull.add_argument("workspace_id")
    p_an_pull.add_argument("experiment_id")
    p_an_pull.add_argument("--iteration", type=int, default=None, help="Restrict to one iteration.")
    p_an_pull.add_argument(
        "--all", action="store_true", help="Include passing traces, not just failures."
    )
    p_an_pull.set_defaults(func=analyze_commands.cmd_analyze_pull)
    p_an_push = analyze_sub.add_parser(
        "push",
        help="Ingest an AnalysisResult (assignments + candidates + hypotheses) from stdin.",
        description=(
            "Read an AnalysisResult JSON on stdin and apply it: stamp failure "
            "modes on traces, create candidate modes, record hypotheses. "
            "Enforces the assignment XOR and classify-don't-rename invariants."
        ),
        epilog="Example:\n  selfevals analyze push ws_01HZZZ... exp_01HXXX... < result.json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_an_push.add_argument("workspace_id")
    p_an_push.add_argument("experiment_id")
    p_an_push.add_argument(
        "--by", default="agent:unknown", help="Provenance stamped on new candidates."
    )
    p_an_push.set_defaults(func=analyze_commands.cmd_analyze_push)
    p_fm = make_subparser(
        sub,
        "failuremode",
        help_text="Manage the workspace failure-mode taxonomy.",
        examples=[
            "selfevals failuremode list ws_01HZZZ... --status candidate",
            "selfevals failuremode promote fm_01HAAA...",
        ],
    )
    fm_sub = p_fm.add_subparsers(dest="failuremode_command", required=True)
    p_fm_list = fm_sub.add_parser(
        "list",
        help="List failure modes in a workspace.",
        description="List the workspace taxonomy; filter by --status.",
        epilog="Example:\n  selfevals failuremode list ws_01HZZZ... --status official",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_fm_list.add_argument("workspace_id")
    p_fm_list.add_argument("--status", choices=["candidate", "official", "retired"], default=None)
    p_fm_list.set_defaults(func=analyze_commands.cmd_failuremode_list)
    p_fm_promote = fm_sub.add_parser(
        "promote",
        help="Promote a candidate mode to official (the human gate).",
        description="Promote a CANDIDATE failure mode to OFFICIAL so it counts.",
        epilog="Example:\n  selfevals failuremode promote ws_01HZZZ... fm_01HAAA...",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_fm_promote.add_argument("workspace_id")
    p_fm_promote.add_argument("failure_mode_id")
    p_fm_promote.set_defaults(func=analyze_commands.cmd_failuremode_promote)
    p_fm_retire = fm_sub.add_parser(
        "retire",
        help="Retire a failure mode (kept for history).",
        description="Mark a failure mode RETIRED; it stays for history.",
        epilog="Example:\n  selfevals failuremode retire ws_01HZZZ... fm_01HAAA...",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_fm_retire.add_argument("workspace_id")
    p_fm_retire.add_argument("failure_mode_id")
    p_fm_retire.set_defaults(func=analyze_commands.cmd_failuremode_retire)
    p_fm_merge = fm_sub.add_parser(
        "merge",
        help="Merge one mode into another (sets superseded_by).",
        description="Move a mode's examples into another and retire the source.",
        epilog="Example:\n  selfevals failuremode merge ws_01HZZZ... fm_dup... --into fm_keep...",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_fm_merge.add_argument("workspace_id")
    p_fm_merge.add_argument("failure_mode_id")
    p_fm_merge.add_argument("--into", required=True, help="Destination mode id.")
    p_fm_merge.set_defaults(func=analyze_commands.cmd_failuremode_merge)
    p_fm_edit = fm_sub.add_parser(
        "edit",
        help="Edit a mode's title and/or definition (human rename action).",
        description="Edit a failure mode's title/definition — the only place a mode is renamed.",
        epilog='Example:\n  selfevals failuremode edit ws_01HZZZ... fm_01HAAA... --title "New title"',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_fm_edit.add_argument("workspace_id")
    p_fm_edit.add_argument("failure_mode_id")
    p_fm_edit.add_argument("--title", default=None)
    p_fm_edit.add_argument("--definition", default=None)
    p_fm_edit.set_defaults(func=analyze_commands.cmd_failuremode_edit)
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
    p_skills_list.set_defaults(func=commands.cmd_skills_list)
    p_skills_path = skills_sub.add_parser(
        "path",
        help="Print the directory of a bundled skill.",
        description="Print the on-disk directory of the named bundled skill.",
        epilog="Example:\n  selfevals skills path error-analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_skills_path.add_argument("name", help="Skill name, e.g. error-analysis.")
    p_skills_path.set_defaults(func=commands.cmd_skills_path)
    p_examples = make_subparser(
        sub,
        "examples",
        help_text="Copy runnable example specs into the current project.",
        examples=[
            "selfevals examples copy pingpong",
            "selfevals run evals/experiments/example_pingpong.yaml --no-persist",
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
    p_examples_copy.add_argument("name", choices=["pingpong"])
    p_examples_copy.add_argument("--to", default=".", help="Destination directory (default: cwd).")
    p_examples_copy.set_defaults(func=commands.cmd_examples_copy)

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
            "selfevals --db ./selfevals.sqlite serve",
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
    p_serve.set_defaults(func=commands.cmd_serve)

    return parser


def app(argv: Sequence[str] | None = None) -> int:
    """Programmatic entry point. Returns the intended process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
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
