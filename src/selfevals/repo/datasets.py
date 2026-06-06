"""Dataset materialization: turn a list of EvalCases into a persisted Dataset.

The `Dataset` schema (`schemas/dataset.py`) has long carried a `manifest_hash`
and a lazy `statistics` field whose docstring promised "Storage layer (PR 2)
sets/invalidates this" — this module is that layer. It computes both as pure
functions and offers a single canonical `persist_dataset` that every entry
point (CLI `dataset create`, API `POST /datasets`, and the inline
materialization in `runner.launch`) shares, so a dataset is created the same way
regardless of who asks. None of it requires an experiment: a dataset is a
first-class resource that can be uploaded and reused on its own.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from selfevals._internal.hashing import content_hash
from selfevals.schemas._base import EntityRef
from selfevals.schemas.dataset import (
    Dataset,
    DatasetStatistics,
    SplitAllocation,
)
from selfevals.schemas.enums import DatasetStatus, DatasetType

if TYPE_CHECKING:
    from selfevals.schemas.eval_case import EvalCase
    from selfevals.storage.interface import WorkspaceScope


def compute_manifest_hash(cases: Sequence[EvalCase]) -> str:
    """A stable `sha256:...` identity for the *content* of a dataset.

    Built from each case's own content (its canonical dump, hashed) keyed by id,
    then sorted by id before hashing — so the manifest hash is invariant to the
    order in which cases were declared and changes iff the set of cases (or any
    case's content) changes. Two datasets with the same cases in any order hash
    identically; this is what makes `persist_dataset` idempotent across reruns.
    """
    per_case = sorted(content_hash(case.model_dump_canonical()) for case in cases)
    return content_hash(per_case)


def compute_statistics(cases: Sequence[EvalCase]) -> DatasetStatistics:
    """Aggregate the portfolio shape of a case list (pure, no storage).

    Counts cases by taxonomy level, primary feature, source, overall risk, and
    PII status, plus the holdout count. Mirrors the dimensions the portfolio
    report (spec §5) and the Dataset schema's `DatasetStatistics` already name.
    """
    by_level: dict[str, int] = {}
    by_feature: dict[str, int] = {}
    by_source: dict[str, int] = {}
    by_risk: dict[str, int] = {}
    pii_breakdown: dict[str, int] = {}
    holdout_count = 0

    for case in cases:
        tax = case.taxonomy
        by_level[str(tax.level)] = by_level.get(str(tax.level), 0) + 1
        primary = tax.feature.primary
        by_feature[primary] = by_feature.get(primary, 0) + 1
        src = str(tax.source.type)
        by_source[src] = by_source.get(src, 0) + 1
        if tax.risk is not None:
            risk = str(tax.risk.overall)
            by_risk[risk] = by_risk.get(risk, 0) + 1
        pii = str(case.metadata.pii_status)
        pii_breakdown[pii] = pii_breakdown.get(pii, 0) + 1
        if case.holdout:
            holdout_count += 1

    return DatasetStatistics(
        total_cases=len(cases),
        by_level=by_level,
        by_feature=by_feature,
        by_source=by_source,
        by_risk=by_risk,
        holdout_count=holdout_count,
        pii_breakdown=pii_breakdown,
    )


def build_dataset(
    *,
    workspace_id: str,
    name: str,
    dataset_type: DatasetType,
    cases: Sequence[EvalCase],
    description: str | None = None,
    split_allocation: SplitAllocation | None = None,
    status: DatasetStatus = DatasetStatus.ACTIVE,
    dataset_id: str | None = None,
    source_dataset_id: str | None = None,
) -> Dataset:
    """Construct a `Dataset` entity over `cases` (does not persist).

    Computes `manifest_hash` and `statistics` up front so the dataset is
    immediately consistent — required because FROZEN/ACTIVE status demands a
    non-empty `manifest_hash`. `cases` are referenced immutably by id+version;
    persisting the EvalCases themselves is the caller's job (see
    `persist_dataset`). The default status is ACTIVE: a freshly materialized
    dataset is ready to be referenced by experiments.
    """
    manifest_hash = compute_manifest_hash(cases)
    statistics = compute_statistics(cases)
    return Dataset(
        id=dataset_id if dataset_id is not None else Dataset.make_id(),
        workspace_id=workspace_id,
        name=name,
        description=description,
        dataset_type=dataset_type,
        cases=[EntityRef(id=case.id, version=case.version) for case in cases],
        split_allocation=split_allocation
        if split_allocation is not None
        else SplitAllocation(),
        source_dataset_id=source_dataset_id,
        manifest_hash=manifest_hash,
        status=status,
        statistics=statistics,
    )


def persist_dataset(
    scope: WorkspaceScope,
    *,
    name: str,
    dataset_type: DatasetType,
    cases: Sequence[EvalCase],
    description: str | None = None,
    split_allocation: SplitAllocation | None = None,
    status: DatasetStatus = DatasetStatus.ACTIVE,
    dataset_id: str | None = None,
    source_dataset_id: str | None = None,
) -> Dataset:
    """Persist `cases` and a `Dataset` over them, returning the Dataset.

    The single canonical path for creating a dataset, shared by the CLI, the
    HTTP API, and inline materialization at launch. It writes each EvalCase and
    then the Dataset manifest into `scope`. Idempotent on re-import of identical
    content: passing a fixed `dataset_id` updates the existing manifest in place
    (the EvalCase ids are stable within the passed list), and the manifest hash
    is unchanged when the case set is unchanged.

    Cases are persisted without an `experiment_id` stamp — a standalone dataset
    belongs to no experiment. The experiment link is made separately, at launch,
    when an experiment references the dataset.
    """
    for case in cases:
        scope.put_entity(case)
    dataset = build_dataset(
        workspace_id=scope.workspace_id,
        name=name,
        dataset_type=dataset_type,
        cases=cases,
        description=description,
        split_allocation=split_allocation,
        status=status,
        dataset_id=dataset_id,
        source_dataset_id=source_dataset_id,
    )
    scope.put_entity(dataset)
    return dataset
