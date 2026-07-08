"""CLI handlers for workspace/experiment/iteration inspection.

Read-only `selfevals workspace show` / `experiment list|show` /
`iteration list` commands — split out of `commands.py` (which keeps the
lifecycle/reporting handlers: init, run, report, compare, estimate).
"""

from __future__ import annotations

import argparse

from selfevals.cli._common import _experiment_iterations, _require_entity, _storage
from selfevals.schemas.experiment import Experiment
from selfevals.schemas.workspace import Workspace
from selfevals.storage.interface import ListFilter


def cmd_workspace_show(args: argparse.Namespace) -> int:
    storage = _storage(args)
    try:
        with storage.open(args.workspace_id) as scope:
            ws = _require_entity(scope, Workspace, args.workspace_id)
            assert isinstance(ws, Workspace)
            experiments = scope.list_entities(Experiment, ListFilter())
        print(f"workspace id={ws.id}")
        print(f"  slug:        {ws.slug}")
        print(f"  name:        {ws.name}")
        print(f"  owner:       {ws.owner_id}")
        print(f"  experiments: {len(experiments)}")
    finally:
        storage.close()
    return 0


def cmd_experiment_list(args: argparse.Namespace) -> int:
    storage = _storage(args)
    try:
        with storage.open(args.workspace_id) as scope:
            experiments = scope.list_entities(Experiment, ListFilter())
    finally:
        storage.close()
    if not experiments:
        print("(no experiments)")
        return 0
    for exp in experiments:
        assert isinstance(exp, Experiment)
        print(f"{exp.id}  state={exp.state}  name={exp.name}")
    return 0


def cmd_experiment_show(args: argparse.Namespace) -> int:
    storage = _storage(args)
    try:
        with storage.open(args.workspace_id) as scope:
            exp = _require_entity(scope, Experiment, args.experiment_id)
            assert isinstance(exp, Experiment)
            iterations = _experiment_iterations(scope, exp.id)
        print(f"experiment id={exp.id}")
        print(f"  name:        {exp.name}")
        print(f"  goal:        {exp.goal}")
        print(f"  state:       {exp.state}")
        print(f"  mode:        {exp.mode}")
        print(f"  proposer:    {exp.proposer.strategy}")
        print(
            f"  target:      {exp.target.primary.name} "
            f"{exp.target.primary.operator} {exp.target.primary.value:g}"
        )
        print(f"  iterations:  {len(iterations)} of {exp.run.max_iterations}")
    finally:
        storage.close()
    return 0


def cmd_iteration_list(args: argparse.Namespace) -> int:
    storage = _storage(args)
    try:
        with storage.open(args.workspace_id) as scope:
            iterations = _experiment_iterations(scope, args.experiment_id)
    finally:
        storage.close()
    if not iterations:
        print("(no iterations)")
        return 0
    for it in iterations:
        primary = it.metrics.primary if it.metrics else None
        primary_str = f"{primary.value:.4g}" if primary else "-"
        decision = it.decision.outcome if it.decision else "-"
        print(f"#{it.iteration:>3} {it.id}  {primary_str:>8}  {decision}")
    return 0
