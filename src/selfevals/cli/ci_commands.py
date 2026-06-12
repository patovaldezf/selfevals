"""CLI handlers for `selfevals baseline` and `selfevals regression`.

The pair implements SF-4 of the SCALING_ROADMAP: a versioned baseline per
experiment plus a regression gate that compares the current run against it and
exits non-zero on a drop, so a CI step can do:

    selfevals regression check ws_... exp_... --iteration itr_... || exit 1

`baseline set/show` manage the `BaselineRecord` pointer; `regression check`
loads the baseline + a current iteration, runs the pure `evaluate_regression`
gate, prints a report, and returns the exit code (0 ok / 1 regressed / 2 user
error via CommandError).
"""

from __future__ import annotations

import argparse

from selfevals.ci.regression import (
    FindingSeverity,
    RegressionResult,
    RegressionThresholds,
    evaluate_regression,
)
from selfevals.cli.commands import (
    CommandError,
    _experiment_iterations,
    _require_entity,
    _storage,
)
from selfevals.schemas.baseline import BaselineRecord
from selfevals.schemas.enums import IterationState
from selfevals.schemas.iteration import IterationMetrics, IterationRecord
from selfevals.storage.interface import ListFilter, WorkspaceScope

# Exit code contract (distinct from CommandError's exit 2 for user errors):
#   0 = no regression, 1 = regression detected, 2 = user error.
EXIT_OK = 0
EXIT_REGRESSED = 1


def _best_completed(scope: WorkspaceScope, experiment_id: str) -> IterationRecord:
    """The COMPLETED iteration with the highest primary value.

    Same selection rule as `OptimizationLoop.best_iteration`
    (optimization/loop.py:95): max by primary metric, but restricted to
    COMPLETED iterations (those guaranteed to carry metrics).
    """
    iterations = [
        it
        for it in _experiment_iterations(scope, experiment_id)
        if it.state == IterationState.COMPLETED and it.metrics is not None
    ]
    if not iterations:
        raise CommandError(
            f"experiment {experiment_id} has no completed iterations with metrics"
        )
    return max(iterations, key=lambda it: it.metrics.primary.value)  # type: ignore[union-attr]


def _current_baseline(scope: WorkspaceScope, experiment_id: str) -> BaselineRecord | None:
    """The most recent BaselineRecord for an experiment, or None if unset."""
    records = [
        r
        for r in scope.list_entities(BaselineRecord, ListFilter())
        if isinstance(r, BaselineRecord) and r.experiment_id == experiment_id
    ]
    if not records:
        return None
    # list_entities defaults to order_by=created_at desc, but re-sort
    # defensively so the "latest wins" rule does not depend on backend order.
    records.sort(key=lambda r: (r.created_at, r.id), reverse=True)
    return records[0]


def _resolve_iteration(
    scope: WorkspaceScope, experiment_id: str, iteration_id: str | None
) -> IterationRecord:
    """Load an explicit iteration by id, or fall back to the best completed one."""
    if iteration_id is None:
        return _best_completed(scope, experiment_id)
    record = _require_entity(scope, IterationRecord, iteration_id)
    assert isinstance(record, IterationRecord)
    if record.experiment_id != experiment_id:
        raise CommandError(
            f"iteration {iteration_id} belongs to experiment {record.experiment_id}, "
            f"not {experiment_id}"
        )
    if record.metrics is None:
        raise CommandError(f"iteration {iteration_id} has no metrics")
    return record


def cmd_baseline_set(args: argparse.Namespace) -> int:
    """Mark an iteration as the experiment's baseline.

    With no `--iteration`, picks the best completed iteration (highest primary),
    matching `best_iteration`. Persists a fresh BaselineRecord (latest wins).
    """
    storage = _storage(args)
    try:
        with storage.open(args.workspace_id) as scope:
            # Confirm the experiment exists for a clean error on a typo'd id.
            iteration = _resolve_iteration(scope, args.experiment_id, args.iteration)
            assert iteration.metrics is not None
            metrics = iteration.metrics
            macro_f1 = metrics.confusion.get("macro_f1") if metrics.confusion else None
            record = BaselineRecord(
                id=BaselineRecord.make_id(),
                workspace_id=args.workspace_id,
                experiment_id=args.experiment_id,
                iteration_id=iteration.id,
                iteration=iteration.iteration,
                primary_metric=metrics.primary.name,
                primary_value=metrics.primary.value,
                macro_f1=macro_f1,
                error_rate=metrics.error_rate,
                note=args.note,
            )
            scope.put_entity(record)
    finally:
        storage.close()

    print(f"baseline set for experiment {args.experiment_id}")
    print(f"  baseline id:    {record.id}")
    print(f"  iteration:      #{record.iteration} ({record.iteration_id})")
    print(f"  {record.primary_metric}: {record.primary_value:.4g}")
    if record.macro_f1 is not None:
        print(f"  macro_f1:       {record.macro_f1:.4g}")
    print(f"  error_rate:     {record.error_rate:.4g}")
    return 0


def cmd_baseline_show(args: argparse.Namespace) -> int:
    """Print an experiment's current baseline (or report there is none)."""
    storage = _storage(args)
    try:
        with storage.open(args.workspace_id) as scope:
            baseline = _current_baseline(scope, args.experiment_id)
    finally:
        storage.close()

    if baseline is None:
        print(f"(no baseline set for experiment {args.experiment_id})")
        return 0
    print(f"baseline for experiment {args.experiment_id}")
    print(f"  baseline id:    {baseline.id}")
    print(f"  iteration:      #{baseline.iteration} ({baseline.iteration_id})")
    print(f"  set at:         {baseline.created_at.isoformat()}")
    print(f"  {baseline.primary_metric}: {baseline.primary_value:.4g}")
    if baseline.macro_f1 is not None:
        print(f"  macro_f1:       {baseline.macro_f1:.4g}")
    print(f"  error_rate:     {baseline.error_rate:.4g}")
    if baseline.note:
        print(f"  note:           {baseline.note}")
    return 0


def _render_regression(result: RegressionResult) -> list[str]:
    lines: list[str] = []
    failures = result.failures
    warnings = result.warnings
    if result.regressed:
        lines.append(f"REGRESSION: {len(failures)} metric(s) dropped past threshold")
    else:
        lines.append("OK: no regression detected")
    lines.append("")
    for finding in result.findings:
        if finding.severity is FindingSeverity.FAIL:
            mark = "FAIL"
        elif finding.severity is FindingSeverity.WARN:
            mark = "WARN"
        else:
            mark = "ok"
        lines.append(f"  [{mark}] {finding.message}")
    if warnings:
        lines.append("")
        lines.append(f"({len(warnings)} warning(s) — not failing the gate)")
    return lines


def cmd_regression_check(args: argparse.Namespace) -> int:
    """Compare a current iteration against the experiment baseline.

    Returns 0 when nothing regressed, 1 when a metric dropped past its
    threshold. User errors (no baseline, bad id) raise CommandError -> exit 2.
    """
    if args.max_primary_drop < 0 or args.max_f1_drop < 0 or args.max_error_rate_rise < 0:
        raise CommandError("thresholds must be >= 0")

    thresholds = RegressionThresholds(
        max_primary_drop=args.max_primary_drop,
        max_f1_drop=args.max_f1_drop,
        max_error_rate_rise=args.max_error_rate_rise,
        error_rate_is_failure=args.fail_on_error_rate,
    )

    storage = _storage(args)
    try:
        with storage.open(args.workspace_id) as scope:
            baseline_record = _current_baseline(scope, args.experiment_id)
            if baseline_record is None:
                raise CommandError(
                    f"no baseline set for experiment {args.experiment_id}; "
                    f"run `selfevals baseline set` first"
                )
            baseline_iter = _require_entity(
                scope, IterationRecord, baseline_record.iteration_id
            )
            assert isinstance(baseline_iter, IterationRecord)
            if baseline_iter.metrics is None:
                raise CommandError(
                    f"baseline iteration {baseline_record.iteration_id} has no metrics"
                )
            baseline_metrics: IterationMetrics = baseline_iter.metrics
            current = _resolve_iteration(scope, args.experiment_id, args.iteration)
            assert current.metrics is not None
            current_metrics: IterationMetrics = current.metrics
    finally:
        storage.close()

    result = evaluate_regression(baseline_metrics, current_metrics, thresholds)

    print(f"regression check — experiment {args.experiment_id}")
    print(
        f"  baseline:  #{baseline_record.iteration} ({baseline_record.iteration_id})"
    )
    print(f"  current:   #{current.iteration} ({current.id})")
    print("")
    for line in _render_regression(result):
        print(line)

    return EXIT_REGRESSED if result.regressed else EXIT_OK
