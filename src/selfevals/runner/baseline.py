"""Dataset-anchored baseline store + auto-set on first run.

The SF-4 design: a regression baseline is anchored to a *dataset*, not an
experiment. The FIRST completed run over a dataset registers its best iteration
as that dataset's baseline, automatically and idempotently. Every later run over
the same dataset (even from another experiment) is graded against that fixed
point — "did the agent regress on this fixed set of cases?".

This module owns the four storage-facing operations the CLI and launch share:

* `baseline_id_for(dataset_id)` — deterministic id, so there is at most one
  baseline per dataset and lookup is a direct `get_entity`.
* `load_baseline(scope, dataset_id)` — fetch it, or `None` if not set yet.
* `set_baseline(...)` — write/overwrite (explicit re-baseline; `selfevals
  baseline set`).
* `maybe_autoset_baseline(scope, spec, result)` — the automatic path: if the
  dataset has no baseline yet, register the run's best iteration. Idempotent (a
  second run is a no-op) and defensive (never raises into the run's hot path —
  it logs and returns).

Why the *best* iteration, not literally the first one: a single run may sweep
several iterations via the optimizer. "How well we did the first time we ran
these cases" is best represented by that run's best result (its
`OptimizationResult.best_iteration`), which is also what the run reports as its
headline. For a single-iteration run (the common CI case) best == only.
"""

from __future__ import annotations

import logging

from selfevals.optimization.loop import OptimizationResult
from selfevals.repo.loader import ExperimentSpec
from selfevals.schemas.dataset import DatasetBaseline
from selfevals.storage.errors import EntityNotFoundError
from selfevals.storage.interface import WorkspaceScope

logger = logging.getLogger(__name__)


def baseline_id_for(dataset_id: str) -> str:
    """Deterministic `DatasetBaseline` id for a dataset.

    Reuses the dataset's ULID suffix under the `dbl_` prefix so the id is a valid
    prefixed-ULID shape and 1:1 with the dataset — one baseline per dataset,
    looked up directly without a scan.
    """
    suffix = dataset_id.split("_", 1)[1] if "_" in dataset_id else dataset_id
    return f"dbl_{suffix}"


def load_baseline(scope: WorkspaceScope, dataset_id: str) -> DatasetBaseline | None:
    """Return the dataset's baseline, or `None` if none has been set."""
    try:
        entity = scope.get_entity(DatasetBaseline, baseline_id_for(dataset_id))
    except EntityNotFoundError:
        return None
    assert isinstance(entity, DatasetBaseline)
    return entity


def set_baseline(
    scope: WorkspaceScope,
    *,
    dataset_id: str,
    iteration_id: str,
    experiment_id: str,
    primary_metric: str,
    primary_value: float,
    error_rate: float = 0.0,
    confusion: dict[str, object] | None = None,
) -> DatasetBaseline:
    """Write (or overwrite) the dataset's baseline. Explicit re-baseline path.

    Used by `selfevals baseline set` and by `maybe_autoset_baseline` once it has
    decided to write. Overwrites any existing baseline (the caller decides
    whether to do that — the auto path checks first; the CLI `set` is explicit).
    Bumps `version` on update so storage's optimistic-concurrency check passes.
    """
    baseline_id = baseline_id_for(dataset_id)
    existing = load_baseline(scope, dataset_id)
    baseline = DatasetBaseline(
        id=baseline_id,
        workspace_id=scope.workspace_id,
        version=(existing.version + 1) if existing is not None else 1,
        dataset_id=dataset_id,
        iteration_id=iteration_id,
        experiment_id=experiment_id,
        primary_metric=primary_metric,
        primary_value=primary_value,
        error_rate=error_rate,
        confusion=confusion,
    )
    scope.put_entity(baseline)
    return baseline


def _dataset_id_for_run(spec: ExperimentSpec) -> str | None:
    """The dataset a run was executed against.

    By the time a run completes, launch has stamped the resolved dataset on
    `experiment.datasets.optimization` — both for `ref:` datasets
    (`_resolve_ref_dataset`) and inline ones (`_materialize_inline_dataset`
    rewrites it to the materialized `ds_…`). So there is always a dataset id to
    anchor to. Returns `None` only if that ref is somehow unset (defensive).
    """
    ref = spec.experiment.datasets.optimization
    return ref.id if ref is not None and ref.id else None


def maybe_autoset_baseline(
    scope: WorkspaceScope,
    spec: ExperimentSpec,
    result: OptimizationResult,
) -> DatasetBaseline | None:
    """Register the run's best iteration as the dataset baseline, if unset.

    The automatic, default path: called after a run persists its iterations.
    Idempotent — if the dataset already has a baseline, this is a no-op (the
    baseline is the FIXED starting point; only an explicit `baseline set`
    re-baselines). Additive and defensive: any storage failure is logged and
    swallowed so the auto-set never fails an otherwise-successful run.

    Returns the baseline it created, `None` if it created nothing (already set,
    no best iteration, missing dataset, or a swallowed error).
    """
    try:
        dataset_id = _dataset_id_for_run(spec)
        if dataset_id is None:
            logger.debug("auto-baseline: run has no anchored dataset; skipping")
            return None

        if load_baseline(scope, dataset_id) is not None:
            # Already baselined — the first run's point of comparison stands.
            return None

        best = result.best_iteration
        if best is None:
            logger.debug("auto-baseline: run produced no iterations; skipping")
            return None

        aggregate = best.aggregate
        confusion = aggregate.confusion.to_dict() if aggregate.confusion is not None else None
        baseline = set_baseline(
            scope,
            dataset_id=dataset_id,
            iteration_id=best.iteration_record.id,
            experiment_id=spec.experiment.id,
            primary_metric=aggregate.primary_metric,
            primary_value=aggregate.primary_value,
            error_rate=aggregate.error_rate,
            confusion=confusion,
        )
        logger.info(
            "auto-baseline: dataset %s baselined from iteration %s "
            "(%s=%.4g, best of run %s)",
            dataset_id,
            baseline.iteration_id,
            baseline.primary_metric,
            baseline.primary_value,
            spec.experiment.id,
        )
        return baseline
    except Exception:  # pragma: no cover - defensive: never break a run
        logger.warning(
            "auto-baseline failed; run is unaffected", exc_info=True
        )
        return None
