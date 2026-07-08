"""Dataset CRUD, baseline, and regression-gate commands."""

from __future__ import annotations

import argparse

from selfevals.cli import baseline_commands, dataset_commands
from selfevals.cli._help import make_subparser


def add_baseline(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p_baseline = make_subparser(
        sub,
        "baseline",
        help_text="Inspect or set a dataset's regression baseline.",
        examples=[
            "selfevals baseline show ws_01HZZZ... --dataset ds_01HXXX...",
            "selfevals baseline set ws_01HZZZ... --dataset ds_01HXXX... --iteration itr_01H...",
        ],
    )
    baseline_sub = p_baseline.add_subparsers(dest="baseline_command", required=True)
    p_bl_show = baseline_sub.add_parser(
        "show",
        help="Show a dataset's current regression baseline.",
        description=(
            "Print the fixed baseline a dataset is graded against. The first run "
            "over a dataset sets this automatically; this only inspects it."
        ),
        epilog="Example:\n  selfevals baseline show ws_01HZZZ... --dataset ds_01HXXX...",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_bl_show.add_argument("workspace_id")
    p_bl_show.add_argument("--dataset", required=True, help="Dataset id (ds_...).")
    p_bl_show.set_defaults(func=baseline_commands.cmd_baseline_show)
    p_bl_set = baseline_sub.add_parser(
        "set",
        help="Explicitly (re-)baseline a dataset from an iteration.",
        description=(
            "Pin an iteration as the dataset's regression baseline, OVERWRITING "
            "any existing one. Use this to raise the bar on purpose — the "
            "automatic baseline on first run never overwrites."
        ),
        epilog=(
            "Example:\n  selfevals baseline set ws_01HZZZ... "
            "--dataset ds_01HXXX... --iteration itr_01H..."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_bl_set.add_argument("workspace_id")
    p_bl_set.add_argument("--dataset", required=True, help="Dataset id (ds_...).")
    p_bl_set.add_argument(
        "--iteration", required=True, help="Iteration id (itr_...) to pin as the baseline."
    )
    p_bl_set.set_defaults(func=baseline_commands.cmd_baseline_set)


def add_regression(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p_regression = make_subparser(
        sub,
        "regression",
        help_text="Gate a run against its dataset's baseline (CI exit codes).",
        examples=[
            "selfevals regression check ws_01HZZZ... --dataset ds_01H... --iteration itr_01H...",
        ],
    )
    regression_sub = p_regression.add_subparsers(dest="regression_command", required=True)
    p_rg_check = regression_sub.add_parser(
        "check",
        help="Compare an iteration against the dataset baseline; exit 1 on regression.",
        description=(
            "Load the dataset's baseline, compare the given iteration against it "
            "(primary metric, per-class F1, error_rate), and exit 0 if ok, 1 if "
            "the agent regressed, 2 on a usage error. The CI gate."
        ),
        epilog=(
            "Example:\n  selfevals regression check ws_01HZZZ... "
            "--dataset ds_01H... --iteration itr_01H..."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_rg_check.add_argument("workspace_id")
    p_rg_check.add_argument("--dataset", required=True, help="Dataset id (ds_...).")
    p_rg_check.add_argument("--iteration", required=True, help="Iteration id (itr_...) to gate.")
    p_rg_check.add_argument(
        "--primary-drop",
        dest="primary_drop",
        type=float,
        default=0.0,
        help="Max allowed drop in the primary metric before failing (default 0.0).",
    )
    p_rg_check.add_argument(
        "--f1-drop",
        dest="f1_drop",
        type=float,
        default=0.05,
        help="Max allowed drop in any per-class F1 before failing (default 0.05).",
    )
    p_rg_check.add_argument(
        "--error-rate-rise",
        dest="error_rate_rise",
        type=float,
        default=0.0,
        help="Max allowed rise in error_rate before failing (default 0.0).",
    )
    p_rg_check.set_defaults(func=baseline_commands.cmd_regression_check)


def add_dataset(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p_dataset = make_subparser(
        sub,
        "dataset",
        help_text="Create, list, inspect, and freeze datasets (no experiment needed).",
        examples=[
            "selfevals dataset create ws_01HZZZ... --from cases.jsonl --name golden-v1",
            "selfevals dataset list ws_01HZZZ...",
            "selfevals dataset show ws_01HZZZ... ds_01HXXX...",
            "selfevals dataset freeze ws_01HZZZ... ds_01HXXX...",
        ],
    )
    ds_sub = p_dataset.add_subparsers(dest="dataset_command", required=True)
    p_ds_create = ds_sub.add_parser(
        "create",
        help="Create a dataset from a JSONL file of cases.",
        description=(
            "Load cases from a JSONL file and persist a standalone dataset "
            "(manifest hash + statistics computed). Runs no experiment."
        ),
        epilog=(
            "Example:\n"
            "  selfevals dataset create ws_01HZZZ... --from cases.jsonl "
            "--name golden-v1 --type golden"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_ds_create.add_argument("workspace_id")
    p_ds_create.add_argument(
        "--from", dest="from_path", required=True, help="Path to a JSONL file (one case per line)."
    )
    p_ds_create.add_argument("--name", required=True, help="Human-readable dataset name.")
    p_ds_create.add_argument(
        "--type",
        default="capability",
        help="Dataset type (e.g. capability, golden, regression, smoke). Default: capability.",
    )
    p_ds_create.add_argument("--description", default=None, help="Optional description.")
    p_ds_create.set_defaults(func=dataset_commands.cmd_dataset_create)
    p_ds_import = ds_sub.add_parser(
        "import",
        help="Alias of `create` — import cases from a JSONL file.",
        description="Alias of `dataset create`: load cases from JSONL into a new dataset.",
        epilog="Example:\n  selfevals dataset import ws_01HZZZ... --from cases.jsonl --name x",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_ds_import.add_argument("workspace_id")
    p_ds_import.add_argument("--from", dest="from_path", required=True)
    p_ds_import.add_argument("--name", required=True)
    p_ds_import.add_argument("--type", default="capability")
    p_ds_import.add_argument("--description", default=None)
    p_ds_import.set_defaults(func=dataset_commands.cmd_dataset_create)
    p_ds_list = ds_sub.add_parser(
        "list",
        help="List datasets in a workspace.",
        description="List every dataset stored in the given workspace.",
        epilog="Example:\n  selfevals dataset list ws_01HZZZ... --status active",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_ds_list.add_argument("workspace_id")
    p_ds_list.add_argument(
        "--status",
        default=None,
        choices=["draft", "frozen", "active", "archived"],
        help="Filter by lifecycle status.",
    )
    p_ds_list.set_defaults(func=dataset_commands.cmd_dataset_list)
    p_ds_show = ds_sub.add_parser(
        "show",
        help="Show one dataset (metadata + statistics).",
        description="Show a dataset's metadata, split allocation, and case statistics.",
        epilog="Example:\n  selfevals dataset show ws_01HZZZ... ds_01HXXX...",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_ds_show.add_argument("workspace_id")
    p_ds_show.add_argument("dataset_id")
    p_ds_show.set_defaults(func=dataset_commands.cmd_dataset_show)
    p_ds_freeze = ds_sub.add_parser(
        "freeze",
        help="Freeze a dataset (recompute manifest, set status=frozen).",
        description=(
            "Recompute the manifest hash and statistics from the current cases "
            "and mark the dataset FROZEN. Regression datasets become immutable."
        ),
        epilog="Example:\n  selfevals dataset freeze ws_01HZZZ... ds_01HXXX...",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_ds_freeze.add_argument("workspace_id")
    p_ds_freeze.add_argument("dataset_id")
    p_ds_freeze.set_defaults(func=dataset_commands.cmd_dataset_freeze)
