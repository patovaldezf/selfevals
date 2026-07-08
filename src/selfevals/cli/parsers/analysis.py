"""Error-analysis handshake and failure-mode taxonomy commands."""

from __future__ import annotations

import argparse

from selfevals.cli import analyze_commands
from selfevals.cli._help import make_subparser


def add_analyze(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
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


def add_failuremode(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
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
