"""Workspace bootstrapping, case persistence, and dataset resolution/materialization."""

from __future__ import annotations

from selfevals._errors import SelfEvalsUserError
from selfevals.repo.datasets import build_dataset
from selfevals.repo.loader import ExperimentSpec, InlineDatasetSource, RefDatasetSource
from selfevals.schemas._base import EntityRef
from selfevals.schemas.dataset import Dataset, SplitAllocation
from selfevals.schemas.eval_case import EvalCase
from selfevals.schemas.workspace import Workspace
from selfevals.storage.interface import ListFilter, StorageInterface, WorkspaceScope


def ensure_workspace_by_id(storage: StorageInterface, workspace_id: str) -> None:
    """Make sure a workspace row exists for `workspace_id`. Idempotent.

    The experiment-free variant of `ensure_workspace`, used by standalone flows
    (uploading a dataset) that have a workspace id but no `ExperimentSpec`.
    """
    with storage.open(workspace_id) as s:
        if s.exists(Workspace, workspace_id):
            return
    ws = Workspace(
        id=workspace_id,
        workspace_id=workspace_id,
        slug=workspace_id.lower(),
        name=workspace_id,
    )
    with storage.open(workspace_id) as s:
        s.put_entity(ws)


def ensure_workspace(storage: StorageInterface, spec: ExperimentSpec) -> None:
    """Make sure the workspace row exists. Idempotent."""
    ensure_workspace_by_id(storage, spec.workspace_id)


def _persist_cases(scope: WorkspaceScope, spec: ExperimentSpec) -> None:
    """Persist the run's eval cases, stamped with the experiment id.

    Cases are an authoring-time construct (they live in the `ExperimentSpec`,
    not storage) — so without this, a launched experiment leaves no trace of
    *which* cases it ran, and `GET .../experiments/{id}/cases` has nothing to
    list. We stamp `experiment_id` (the loader leaves it None) and persist
    every case, holdout included: the cases endpoint reports the full set and
    flags holdout, rather than hiding reserved cases.

    Idempotent across reruns of the same experiment: `EvalCase` ids are stable
    within a spec (the loader assigns them once), so `put_entity` updates the
    existing row in place rather than duplicating.
    """
    for case in spec.cases:
        if case.experiment_id != spec.experiment.id:
            case.experiment_id = spec.experiment.id
        scope.put_entity(case)


def _resolve_dataset_source(
    scope: WorkspaceScope | None, spec: ExperimentSpec
) -> SplitAllocation | None:
    """Resolve the experiment's dataset source, returning its split allocation.

    * Inline: cases already live in `spec.cases`; the split (if any) rides the
      block. Returns it so the loop's sampler honors it.
    * Ref: load the persisted `Dataset`, hydrate `spec.cases` in place from its
      case refs, and return its split. A ref needs storage — resolving one
      without a `scope` (an ephemeral run) is a user error.

    Mutates `spec.cases` in place (it's a list on a frozen dataclass) so every
    downstream reader — graders, persistence, the loop — sees the resolved set.
    """
    source = spec.dataset_source
    if isinstance(source, InlineDatasetSource):
        return source.split_allocation
    if isinstance(source, RefDatasetSource):
        if scope is None:
            raise SelfEvalsUserError(
                f"experiment references dataset {source.ref.id!r} but the run has no "
                "storage — a dataset reference needs storage to resolve. Declare the "
                "cases inline in the spec instead."
            )
        return _resolve_ref_dataset(scope, spec, source.ref)
    return None  # defensive: unknown source variant


def _resolve_ref_dataset(
    scope: WorkspaceScope, spec: ExperimentSpec, ref: EntityRef
) -> SplitAllocation | None:
    try:
        dataset = scope.get_entity(Dataset, ref.id)
    except Exception as exc:
        raise SelfEvalsUserError(
            f"experiment references dataset {ref.id!r}, which was not found in "
            f"workspace {spec.workspace_id!r}. Create it first "
            "(`selfevals dataset create` or POST .../datasets)."
        ) from exc
    assert isinstance(dataset, Dataset)

    ref_ids = {c.id for c in dataset.cases}
    resolved = [
        c
        for c in scope.list_entities(EvalCase, ListFilter())
        if isinstance(c, EvalCase) and c.id in ref_ids
    ]
    if not resolved:
        raise SelfEvalsUserError(
            f"dataset {ref.id!r} resolved to zero cases — its EvalCases are missing "
            "from storage. Re-import the dataset."
        )
    # Hydrate the (frozen-dataclass) spec's case list in place.
    spec.cases[:] = resolved
    # Stamp the experiment's optimization ref so reports/links point at the
    # actual dataset (the YAML may have named a placeholder).
    spec.experiment.datasets.optimization = EntityRef(id=dataset.id, version=dataset.version)
    spec.experiment.frozen.datasets = [EntityRef(id=dataset.id, version=dataset.version)]
    return dataset.split_allocation


def _materialize_inline_dataset(scope: WorkspaceScope, spec: ExperimentSpec) -> None:
    """Persist a real Dataset over an inline experiment's cases and link it.

    The `dataset:` block's inline cases used to vanish into the run with no
    Dataset entity — leaving `experiment.datasets.optimization` pointing at a
    placeholder that never existed. Here we materialize one: build a Dataset
    over the just-persisted cases (manifest hash + statistics) and rewrite the
    experiment's dataset refs (`datasets.optimization`, `frozen.datasets[0]`) to
    point at it. Ref-sourced experiments are resolved elsewhere (F6) and skipped.

    Idempotent across reruns: the dataset id is derived from the experiment id,
    so a second launch updates the same Dataset row in place rather than
    spawning a new one. Cases are already in storage (`_persist_cases`), so this
    only writes the manifest.
    """
    source = spec.dataset_source
    if not isinstance(source, InlineDatasetSource) or not spec.cases:
        return

    dataset_id = _inline_dataset_id(spec.experiment.id)
    dataset_type = source.dataset_type
    if dataset_type is None:
        # No declared type — adopt the cases' shared dataset_type (they all carry
        # one via taxonomy; inline suites are typically homogeneous).
        dataset_type = spec.cases[0].taxonomy.dataset_type
    name = source.name or f"{spec.experiment.name} dataset"

    existing_version = 1
    try:
        prior = scope.get_entity(Dataset, dataset_id)
        if isinstance(prior, Dataset):
            existing_version = prior.version
    except Exception:
        existing_version = 0  # not yet persisted → fresh insert at version 1

    dataset = build_dataset(
        workspace_id=spec.workspace_id,
        name=name,
        dataset_type=dataset_type,
        cases=spec.cases,
        description=source.description,
        split_allocation=source.split_allocation,
        dataset_id=dataset_id,
    )
    if existing_version:
        dataset.version = existing_version
    scope.put_entity(dataset)

    ref = EntityRef(id=dataset.id, version=dataset.version)
    spec.experiment.datasets.optimization = ref
    spec.experiment.frozen.datasets = [ref]


def _inline_dataset_id(experiment_id: str) -> str:
    """A stable Dataset id for an experiment's inline cases (idempotent reruns).

    Reuses the experiment's ULID suffix under the `ds_` prefix so the id is
    valid (prefixed ULID shape) and 1:1 with the experiment — relaunching the
    same experiment rewrites the same Dataset row.
    """
    suffix = experiment_id.split("_", 1)[1] if "_" in experiment_id else experiment_id
    return f"ds_{suffix}"
