"""Workspace, experiment, and iteration inspection commands."""

from __future__ import annotations

import argparse

from selfevals.cli import experiment_commands
from selfevals.cli._help import make_subparser


def add_workspace(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
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
    p_ws_show.set_defaults(func=experiment_commands.cmd_workspace_show)


def add_experiment(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
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
    p_exp_list.set_defaults(func=experiment_commands.cmd_experiment_list)
    p_exp_show = exp_sub.add_parser(
        "show",
        help="Show one experiment.",
        description="Show one experiment's spec, target, and iteration count.",
        epilog="Example:\n  selfevals experiment show ws_01HZZZ... exp_01HXXX...",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_exp_show.add_argument("workspace_id")
    p_exp_show.add_argument("experiment_id")
    p_exp_show.set_defaults(func=experiment_commands.cmd_experiment_show)


def add_iteration(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
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
    p_iter_list.set_defaults(func=experiment_commands.cmd_iteration_list)
