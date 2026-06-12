"""CLI handlers for `selfevals baseline` and `selfevals regression`.

The regression gate is anchored to a *dataset*: the first run over a dataset
auto-registers its baseline (see `runner.baseline`), and every later run is
graded against that fixed point. These commands expose the manual surface:

* `baseline show --dataset <ds>` — inspect the dataset's current baseline.
* `baseline set --dataset <ds> [--iteration <itr>]` — explicitly (re-)baseline,
  raising the bar on purpose. Overwrites; the auto-set on first run does not.
* `regression check --dataset <ds> --iteration <itr>` — compare an iteration
  against the dataset's baseline. Exit 0 = ok, 1 = regression, 2 = usage error.

The math lives in `selfevals.ci.regression` (pure, storage-agnostic); these
handlers only load entities and render the verdict.
"""

from __future__ import annotations

import argparse

from selfevals.ci.regression import (
    BaselineMetrics,
    RegressionThresholds,
    evaluate_regression,
)
from selfevals.cli.commands import CommandError, _storage
from selfevals.runner.baseline import load_baseline, set_baseline
from selfevals.schemas.dataset import Dataset, DatasetBaseline
from selfevals.schemas.iteration import IterationRecord
from selfevals.storage.errors import EntityNotFoundError
from selfevals.storage.interface import WorkspaceScope


def _require_dataset(scope: WorkspaceScope, dataset_id: str) -> Dataset:
    try:
        entity = scope.get_entity(Dataset, dataset_id)
    except EntityNotFoundError as exc:
        raise CommandError(
            f"dataset {dataset_id!r} not found in workspace {scope.workspace_id!r}"
        ) from exc
    assert isinstance(entity, Dataset)
    return entity


def _require_iteration(scope: WorkspaceScope, iteration_id: str) -> IterationRecord:
    try:
        entity = scope.get_entity(IterationRecord, iteration_id)
    except EntityNotFoundError as exc:
        raise CommandError(
            f"iteration {iteration_id!r} not found in workspace {scope.workspace_id!r}"
        ) from exc
    assert isinstance(entity, IterationRecord)
    return entity


def _metrics_from_iteration(record: IterationRecord) -> BaselineMetrics:
    """Project an IterationRecord's persisted metrics into the gate's input."""
    if record.metrics is None:
        raise CommandError(
            f"iteration {record.id!r} has no metrics (state={record.state}); "
            "only completed iterations can be gated"
        )
    m = record.metrics
    return BaselineMetrics.from_confusion(
        primary_metric=m.primary.name,
        primary_value=m.primary.value,
        error_rate=m.error_rate,
        confusion=m.confusion,
    )


def cmd_baseline_show(args: argparse.Namespace) -> int:
    """Print the dataset's current regression baseline (or report none)."""
    storage = _storage(args)
    try:
        with storage.open(args.workspace_id) as scope:
            _require_dataset(scope, args.dataset)
            baseline = load_baseline(scope, args.dataset)
            if baseline is None:
                print(f"no baseline set for dataset {args.dataset}")
                print("  (the first run over this dataset sets it automatically,")
                print("   or set one explicitly: selfevals baseline set --dataset <ds>)")
                return 0
            _print_baseline(baseline)
    finally:
        storage.close()
    return 0


def _print_baseline(baseline: DatasetBaseline) -> None:
    print(f"baseline for dataset {baseline.dataset_id}")
    print(f"  iteration:   {baseline.iteration_id}")
    print(f"  experiment:  {baseline.experiment_id}")
    print(f"  {baseline.primary_metric}: {baseline.primary_value:.4g}")
    print(f"  error_rate:  {baseline.error_rate:.4g}")
    if baseline.confusion:
        per_label = baseline.confusion.get("per_label_f1")
        if isinstance(per_label, dict) and per_label:
            shown = ", ".join(
                f"{label}={value:.4g}"
                for label, value in sorted(per_label.items())
                if value is not None
            )
            if shown:
                print(f"  per-class F1: {shown}")
    print(f"  set at:      {baseline.updated_at.isoformat()}")


def cmd_baseline_set(args: argparse.Namespace) -> int:
    """Explicitly (re-)baseline a dataset from an iteration.

    Overwrites any existing baseline — this is the intentional "raise the bar"
    path, distinct from the idempotent auto-set on first run. With `--iteration`
    the named iteration is used; without it, the dataset's existing baseline must
    already exist (nothing to infer otherwise — point at an iteration).
    """
    if not args.iteration:
        raise CommandError(
            "baseline set requires --iteration <itr_id> "
            "(the iteration to pin as the new baseline)"
        )
    storage = _storage(args)
    try:
        with storage.open(args.workspace_id) as scope:
            _require_dataset(scope, args.dataset)
            record = _require_iteration(scope, args.iteration)
            metrics = _metrics_from_iteration(record)
            baseline = set_baseline(
                scope,
                dataset_id=args.dataset,
                iteration_id=record.id,
                experiment_id=record.experiment_id,
                primary_metric=metrics.primary_metric,
                primary_value=metrics.primary_value,
                error_rate=metrics.error_rate,
                confusion=record.metrics.confusion if record.metrics else None,
            )
            print(f"baseline set for dataset {args.dataset}")
            _print_baseline(baseline)
    finally:
        storage.close()
    return 0


def cmd_regression_check(args: argparse.Namespace) -> int:
    """Gate an iteration against its dataset's baseline.

    Exit 0 = no regression, 1 = regression detected, 2 = usage error (no
    baseline, missing entity — raised as CommandError → exit 2 by the dispatcher).
    """
    thresholds = RegressionThresholds(
        primary_drop=args.primary_drop,
        per_class_f1_drop=args.f1_drop,
        error_rate_rise=args.error_rate_rise,
    )
    storage = _storage(args)
    try:
        with storage.open(args.workspace_id) as scope:
            _require_dataset(scope, args.dataset)
            baseline = load_baseline(scope, args.dataset)
            if baseline is None:
                raise CommandError(
                    f"dataset {args.dataset!r} has no baseline to gate against. "
                    "Run it once (auto-baselines), or `selfevals baseline set`."
                )
            record = _require_iteration(scope, args.iteration)
            current = _metrics_from_iteration(record)
            baseline_metrics = BaselineMetrics.from_confusion(
                primary_metric=baseline.primary_metric,
                primary_value=baseline.primary_value,
                error_rate=baseline.error_rate,
                confusion=baseline.confusion,
            )
    finally:
        storage.close()

    result = evaluate_regression(baseline_metrics, current, thresholds)
    _render_regression(result, baseline=baseline, iteration_id=args.iteration)
    return 1 if result.regressed else 0


def _render_regression(result: object, *, baseline: DatasetBaseline, iteration_id: str) -> None:
    from selfevals.ci.regression import RegressionResult

    assert isinstance(result, RegressionResult)
    print(f"regression check: dataset {baseline.dataset_id}")
    print(f"  baseline iteration: {baseline.iteration_id}")
    print(f"  current iteration:  {iteration_id}")
    print(f"  verdict: {result.summary()}")
    for finding in result.findings:
        mark = "FAIL" if finding.regressed else "ok  "
        print(f"    [{mark}] {finding.detail}")
