"""Shared CLI helpers used across the `cli/*_commands.py` handler modules.

Split out of `commands.py` so handler modules (`experiment_commands.py`,
`ops_commands.py`, `dataset_commands.py`, `analyze_commands.py`, ...) and the
API's read-model queries (`api/queries/`) can depend on this narrow surface
instead of the full command-handler module.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from selfevals._errors import SelfEvalsUserError
from selfevals.graders._confusion import ConfusionReport
from selfevals.optimization.aggregator import FunnelNode, IterationAggregate
from selfevals.optimization.loop import IterationOutcome, OptimizationResult
from selfevals.runner.executor import CaseRun, RepetitionResult
from selfevals.schemas.experiment import Experiment
from selfevals.schemas.iteration import DecisionRecord, IterationRecord
from selfevals.schemas.trace import Trace
from selfevals.storage.factory import open_storage, resolve_storage_url
from selfevals.storage.interface import ListFilter, StorageInterface

if TYPE_CHECKING:
    from selfevals.schemas._base import BaseEntity


class CommandError(SelfEvalsUserError):
    """Raised for user-correctable errors. CLI prints and exits 2.

    Thin alias of :class:`selfevals._errors.SelfEvalsUserError` so the
    rest of the CLI keeps the historical name. Anything new outside
    the CLI should raise :class:`SelfEvalsUserError` directly.
    """


def _storage(args: argparse.Namespace) -> StorageInterface:
    """Open the configured Postgres storage.

    A connection error surfaces as the underlying psycopg exception; we
    don't translate it here (there's no single-file corruption/lock case to
    rewrite the way the old SQLite path did).
    """
    return open_storage(resolve_storage_url(args.db))


def _ensure_cwd_on_path() -> None:
    """Make the user's project root importable when the CLI runs.

    `uv run selfevals ...` invokes a console script whose `sys.path` does
    not include the cwd, so agent entrypoints like
    `examples.hello_llm.agent:run` would fail to import. We insert the
    cwd at the front of `sys.path` (once) so the resolver sees user
    packages.
    """
    cwd = str(Path.cwd())
    if cwd not in sys.path:
        sys.path.insert(0, cwd)


def _require_entity(scope: object, entity_type: type[BaseEntity], entity_id: str) -> BaseEntity:
    try:
        return scope.get_entity(entity_type, entity_id)  # type: ignore[attr-defined,no-any-return]
    except Exception as exc:
        raise CommandError(f"{entity_type.__name__} {entity_id} not found in workspace") from exc


def _experiment_iterations(scope: object, experiment_id: str) -> list[IterationRecord]:
    listed = scope.list_entities(IterationRecord, ListFilter())  # type: ignore[attr-defined]
    iterations = [
        it for it in listed if isinstance(it, IterationRecord) and it.experiment_id == experiment_id
    ]
    iterations.sort(key=lambda it: it.iteration)
    return iterations


def _experiment_decisions(scope: object, experiment_id: str) -> dict[int, DecisionRecord]:
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


def _load_case_runs(scope: object, experiment_id: str, iteration: int) -> list[CaseRun]:
    """Rehydrate an iteration's CaseRuns from persisted Trace entities.

    `run.persist_traces` writes each repetition's Trace (stamped with its
    grader_results) to storage; the IterationRecord only keeps the trace ids.
    Without re-reading the traces, a report rebuilt from disk would have empty
    `case_runs` and therefore empty `failure_reasons` — losing the per-grade
    rationales that an inline `run --format json` shows. Filter Trace entities
    by experiment+iteration (default `persist_traces=failed` keeps exactly the
    non-passing ones the reporter dedups), group by eval_case_id, and rebuild
    minimal CaseRuns. `response`/`error` stay None — the reporter only reads
    `trace.grader_results`.
    """
    listed = scope.list_entities(  # type: ignore[attr-defined]
        Trace,
        ListFilter(where={"run.experiment_id": experiment_id, "run.iteration": iteration}),
    )
    by_case: dict[str, list[RepetitionResult]] = {}
    for tr in listed:
        if not isinstance(tr, Trace):
            continue
        case_id = tr.run.eval_case_id or tr.run.run_id
        by_case.setdefault(case_id, []).append(
            RepetitionResult(repetition=tr.run.repetition, trace=tr, response=None, error=None)
        )
    case_runs: list[CaseRun] = []
    for case_id, reps in by_case.items():
        reps.sort(key=lambda r: r.repetition)
        case_runs.append(CaseRun(case_id=case_id, repetitions=reps))
    return case_runs


def _reconstruct_result(
    scope: object,
    experiment: Experiment,
    iterations: Sequence[IterationRecord],
    decisions: dict[int, DecisionRecord],
) -> OptimizationResult:
    """Build an OptimizationResult from persisted state.

    Aggregate-level fields come from the IterationRecord; `case_runs` are
    rehydrated from persisted Traces (see `_load_case_runs`) so the report's
    `failure_reasons` match an inline `run --format json`. Live-only fields the
    traces don't carry (e.g. the AdapterResponse) stay absent — the reporter
    doesn't read them.
    """
    from selfevals.schemas.iteration import Proposal

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
            failure_mode_counts=dict(record.metrics.failure_mode_counts),
            total_cost_usd=record.metrics.cost_usd or 0.0,
            total_duration_ms=int((record.metrics.duration_seconds or 0.0) * 1000),
            error_rate=record.metrics.error_rate,
            case_count=int(record.execution.ran_against.get("case_count", 0)),
            # Rehydrate the persisted funnel so a result reconstructed from
            # storage carries the same grader breakdown a live run does — the
            # reporter's `funnel` is no longer always empty here.
            funnel={
                key: FunnelNode.from_dict(node)
                for key, node in record.metrics.funnel.items()
            },
            # Rehydrate the persisted confusion matrix so a reconstructed result
            # carries the same NxN matrix a live run does (reporter renders it).
            confusion=(
                ConfusionReport.from_dict(record.metrics.confusion)
                if record.metrics.confusion is not None
                else None
            ),
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
                case_runs=_load_case_runs(scope, experiment.id, record.iteration),
                iteration_record=record,
                decision_record=decision,
            )
        )
    return OptimizationResult(
        experiment=experiment,
        iterations=outcomes,
        terminated_reason="loaded_from_storage",
    )
