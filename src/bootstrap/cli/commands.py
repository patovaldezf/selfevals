"""CLI command implementations.

Each `cmd_*` takes the parsed argparse Namespace and returns an int exit
code. Errors that should produce a clean `error: <msg>` line raise
`CommandError`; anything else escapes as a traceback (a real bug).
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from bootstrap.optimization.aggregator import IterationAggregate
from bootstrap.optimization.loop import IterationOutcome, OptimizationResult
from bootstrap.reporter import render_json, render_markdown
from bootstrap.schemas.experiment import Experiment
from bootstrap.schemas.iteration import DecisionRecord, IterationRecord
from bootstrap.schemas.workspace import Workspace
from bootstrap.storage.interface import ListFilter
from bootstrap.storage.seed import seed_workspace
from bootstrap.storage.sqlite import SQLiteStorage

if TYPE_CHECKING:
    from bootstrap.schemas._base import BaseEntity


class CommandError(Exception):
    """Raised for user-correctable errors. CLI prints and exits 2."""


def _storage(args: argparse.Namespace) -> SQLiteStorage:
    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return SQLiteStorage(db_path)


# --- init ---


def cmd_init(args: argparse.Namespace) -> int:
    storage = _storage(args)
    try:
        seeded = seed_workspace(
            storage,
            slug=args.slug,
            name=args.name or args.slug,
            user_id=args.user,
        )
    finally:
        storage.close()
    ws = seeded.workspace
    print(f"workspace id={ws.id} slug={ws.slug} name={ws.name}")
    print(f"members: {len(seeded.members)} role(s)")
    return 0


# --- workspace ---


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


# --- experiment ---


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
        print(f"  target:      {exp.target.primary.name} "
              f"{exp.target.primary.operator} {exp.target.primary.value:g}")
        print(f"  iterations:  {len(iterations)} of {exp.run.max_iterations}")
    finally:
        storage.close()
    return 0


# --- iteration ---


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


# --- report ---


def cmd_report(args: argparse.Namespace) -> int:
    storage = _storage(args)
    try:
        with storage.open(args.workspace_id) as scope:
            exp = _require_entity(scope, Experiment, args.experiment_id)
            assert isinstance(exp, Experiment)
            iterations = _experiment_iterations(scope, exp.id)
            decisions = _experiment_decisions(scope, exp.id)
    finally:
        storage.close()

    result = _reconstruct_result(exp, iterations, decisions)
    if args.format == "json":
        print(render_json(result))
    else:
        print(render_markdown(result))
    return 0


# --- compare ---


def cmd_compare(args: argparse.Namespace) -> int:
    storage = _storage(args)
    try:
        with storage.open(args.workspace_id) as scope:
            a = _require_entity(scope, IterationRecord, args.iter_a_id)
            b = _require_entity(scope, IterationRecord, args.iter_b_id)
    finally:
        storage.close()
    assert isinstance(a, IterationRecord)
    assert isinstance(b, IterationRecord)
    if a.experiment_id != b.experiment_id:
        raise CommandError(
            f"iterations belong to different experiments "
            f"({a.experiment_id} vs {b.experiment_id})"
        )

    a_primary = a.metrics.primary if a.metrics else None
    b_primary = b.metrics.primary if b.metrics else None
    if a_primary is None or b_primary is None:
        raise CommandError("one of the iterations has no metrics")
    if a_primary.name != b_primary.name:
        raise CommandError(
            f"iterations report different primary metrics "
            f"({a_primary.name} vs {b_primary.name})"
        )

    delta = b_primary.value - a_primary.value
    print(f"primary metric: {a_primary.name}")
    print(f"  A (#{a.iteration:>3})  {a_primary.value:.4g}")
    print(f"  B (#{b.iteration:>3})  {b_primary.value:.4g}")
    print(f"  Δ           {delta:+.4g}")
    if a.decision and b.decision:
        print(f"decision: {a.decision.outcome}  →  {b.decision.outcome}")
    return 0


# --- estimate ---


def cmd_estimate(args: argparse.Namespace) -> int:
    if args.cases < 1 or args.space_size < 1 or args.reps < 1:
        raise CommandError("cases, space-size, and reps must all be >= 1")
    if args.cost_per_call < 0:
        raise CommandError("cost-per-call must be >= 0")
    total_calls = args.cases * args.reps * args.space_size
    total_cost = total_calls * args.cost_per_call
    print(f"cases x reps x proposals = {args.cases} x {args.reps} x {args.space_size}")
    print(f"agent calls (upper bound): {total_calls}")
    print(f"estimated cost (USD):      ${total_cost:.2f}")
    return 0


# --- internals ---


def _require_entity(
    scope: object, entity_type: type[BaseEntity], entity_id: str
) -> BaseEntity:
    try:
        return scope.get_entity(entity_type, entity_id)  # type: ignore[attr-defined,no-any-return]
    except Exception as exc:
        raise CommandError(
            f"{entity_type.__name__} {entity_id} not found in workspace"
        ) from exc


def _experiment_iterations(
    scope: object, experiment_id: str
) -> list[IterationRecord]:
    listed = scope.list_entities(IterationRecord, ListFilter())  # type: ignore[attr-defined]
    iterations = [
        it for it in listed if isinstance(it, IterationRecord) and it.experiment_id == experiment_id
    ]
    iterations.sort(key=lambda it: it.iteration)
    return iterations


def _experiment_decisions(
    scope: object, experiment_id: str
) -> dict[int, DecisionRecord]:
    listed = scope.list_entities(DecisionRecord, ListFilter())  # type: ignore[attr-defined]
    by_iter: dict[int, DecisionRecord] = {}
    for d in listed:
        if not isinstance(d, DecisionRecord):
            continue
        if d.experiment_id != experiment_id:
            continue
        # Latest wins on duplicate (shouldn't happen in MVP).
        by_iter[d.iteration] = d
    return by_iter


def _reconstruct_result(
    experiment: Experiment,
    iterations: Sequence[IterationRecord],
    decisions: dict[int, DecisionRecord],
) -> OptimizationResult:
    """Build an OptimizationResult from persisted state.

    Some live-only fields (case_runs, per-case GradeResults) are lost
    once iterations hit disk — we surface what survives. The reporter
    only uses aggregate-level fields, so the report fidelity is intact.
    """
    from bootstrap.schemas.iteration import Proposal

    outcomes: list[IterationOutcome] = []
    for record in iterations:
        if record.metrics is None:
            continue
        primary = record.metrics.primary
        guardrails = {g.name: g.value for g in record.metrics.guardrails}
        reliability = dict(record.metrics.reliability)
        aggregate = IterationAggregate(
            primary_metric=primary.name,
            primary_value=primary.value,
            guardrails=guardrails,
            reliability=reliability,
            total_cost_usd=record.metrics.cost_usd or 0.0,
            total_duration_ms=int((record.metrics.duration_seconds or 0.0) * 1000),
            case_count=int(record.execution.ran_against.get("case_count", 0)),
        )
        decision = decisions.get(record.iteration)
        if decision is None:
            # Defensive: skip orphans so the loop's invariants aren't violated.
            continue
        proposal = Proposal(
            parameters=dict(record.proposed_parameters),
            hypothesis=record.hypothesis,
        )
        outcomes.append(
            IterationOutcome(
                iteration=record.iteration,
                proposal=proposal,
                aggregate=aggregate,
                case_runs=[],
                iteration_record=record,
                decision_record=decision,
            )
        )
    return OptimizationResult(
        experiment=experiment,
        iterations=outcomes,
        terminated_reason="loaded_from_storage",
    )
