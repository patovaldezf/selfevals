"""HTTP-facing baseline + regression operations (loop-closer 2B).

Wraps `runner/baseline.py` (load/set) and `ci/regression.py` (evaluate) for the
API, anchored to a *dataset* exactly like the CLI (`baseline_commands.py`). The
regression math stays in `ci.regression` (pure); this module only loads
entities, projects them into the gate's input, and shapes the response.
"""

from __future__ import annotations

from selfevals.api.schemas import (
    BaselineResponse,
    RegressionFindingResponse,
    RegressionResultResponse,
)
from selfevals.ci.regression import (
    BaselineMetrics,
    RegressionThresholds,
    evaluate_regression,
)
from selfevals.runner.baseline import load_baseline, set_baseline
from selfevals.schemas.dataset import Dataset, DatasetBaseline
from selfevals.schemas.iteration import IterationRecord
from selfevals.storage.errors import EntityNotFoundError
from selfevals.storage.interface import StorageInterface, WorkspaceScope


class BaselineNotFoundError(Exception):
    """No baseline is set for the dataset yet."""


class BaselineOpError(Exception):
    """A baseline/regression operation could not be completed (bad input)."""


def _require_dataset(scope: WorkspaceScope, dataset_id: str) -> Dataset:
    try:
        entity = scope.get_entity(Dataset, dataset_id)
    except EntityNotFoundError as exc:
        raise BaselineOpError(f"dataset {dataset_id} not found") from exc
    assert isinstance(entity, Dataset)
    return entity


def _require_iteration(scope: WorkspaceScope, iteration_id: str) -> IterationRecord:
    try:
        entity = scope.get_entity(IterationRecord, iteration_id)
    except EntityNotFoundError as exc:
        raise BaselineOpError(f"iteration {iteration_id} not found") from exc
    assert isinstance(entity, IterationRecord)
    return entity


def _metrics_from_iteration(record: IterationRecord) -> BaselineMetrics:
    if record.metrics is None:
        raise BaselineOpError(
            f"iteration {record.id} has no metrics (state={record.state}); "
            "only completed iterations can be baselined or gated"
        )
    m = record.metrics
    return BaselineMetrics.from_confusion(
        primary_metric=m.primary.name,
        primary_value=m.primary.value,
        error_rate=m.error_rate,
        confusion=m.confusion,
    )


def _view(baseline: DatasetBaseline) -> BaselineResponse:
    return BaselineResponse(
        dataset_id=baseline.dataset_id,
        iteration_id=baseline.iteration_id,
        experiment_id=baseline.experiment_id,
        primary_metric_name=baseline.primary_metric,
        primary_metric_value=baseline.primary_value,
        error_rate=baseline.error_rate,
        created_at=baseline.updated_at,
    )


def get_baseline(
    storage: StorageInterface, *, workspace_id: str, dataset_id: str
) -> BaselineResponse:
    with storage.open(workspace_id) as scope:
        _require_dataset(scope, dataset_id)
        baseline = load_baseline(scope, dataset_id)
        if baseline is None:
            raise BaselineNotFoundError(f"dataset {dataset_id} has no baseline")
        return _view(baseline)


def set_dataset_baseline(
    storage: StorageInterface,
    *,
    workspace_id: str,
    dataset_id: str,
    iteration_id: str | None,
) -> BaselineResponse:
    with storage.open(workspace_id) as scope:
        _require_dataset(scope, dataset_id)
        if iteration_id is None:
            # Re-anchor to the existing baseline's iteration is meaningless;
            # the CLI requires an explicit iteration, so we do too.
            raise BaselineOpError(
                "provide iteration_id (the iteration to pin as the new baseline)"
            )
        record = _require_iteration(scope, iteration_id)
        metrics = _metrics_from_iteration(record)
        baseline = set_baseline(
            scope,
            dataset_id=dataset_id,
            iteration_id=record.id,
            experiment_id=record.experiment_id,
            primary_metric=metrics.primary_metric,
            primary_value=metrics.primary_value,
            error_rate=metrics.error_rate,
            confusion=record.metrics.confusion if record.metrics else None,
        )
        return _view(baseline)


def run_regression_check(
    storage: StorageInterface,
    *,
    workspace_id: str,
    dataset_id: str,
    iteration_id: str,
    primary_drop: float,
    per_class_f1_drop: float,
    error_rate_rise: float,
) -> RegressionResultResponse:
    thresholds = RegressionThresholds(
        primary_drop=primary_drop,
        per_class_f1_drop=per_class_f1_drop,
        error_rate_rise=error_rate_rise,
    )
    with storage.open(workspace_id) as scope:
        _require_dataset(scope, dataset_id)
        baseline = load_baseline(scope, dataset_id)
        if baseline is None:
            raise BaselineNotFoundError(
                f"dataset {dataset_id} has no baseline to gate against"
            )
        record = _require_iteration(scope, iteration_id)
        current = _metrics_from_iteration(record)
        baseline_metrics = BaselineMetrics.from_confusion(
            primary_metric=baseline.primary_metric,
            primary_value=baseline.primary_value,
            error_rate=baseline.error_rate,
            confusion=baseline.confusion,
        )

    result = evaluate_regression(baseline_metrics, current, thresholds)
    return RegressionResultResponse(
        dataset_id=dataset_id,
        iteration_id=iteration_id,
        regressed=result.regressed,
        findings=[
            RegressionFindingResponse(
                signal=f.signal,
                baseline=f.baseline,
                current=f.current,
                delta=f.delta,
                regressed=f.regressed,
                detail=f.detail,
            )
            for f in result.findings
        ],
    )
