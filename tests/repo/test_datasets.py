"""Tests for repo.datasets: pure hash/statistics + the canonical persist path.

These cover the "storage layer (PR 2)" the Dataset schema's docstring promised:
a deterministic manifest hash, portfolio statistics, and a `persist_dataset`
that writes cases + manifest to a scope without any experiment in sight.
"""

from __future__ import annotations

import pytest

from selfevals.repo.datasets import (
    build_dataset,
    compute_manifest_hash,
    compute_statistics,
    persist_dataset,
)
from selfevals.schemas.dataset import Dataset, SplitAllocation
from selfevals.schemas.enums import (
    DatasetSource,
    DatasetStatus,
    DatasetType,
    GroundTruthMethod,
    Level,
    PIIStatus,
)
from selfevals.schemas.eval_case import (
    CaseMetadata,
    CaseTaxonomy,
    EvalCase,
    Expected,
    FeatureTag,
    GroundTruthSpec,
    SourceInfo,
)
from selfevals.schemas.workspace import Workspace
from selfevals.storage.errors import WorkspaceMismatchError
from selfevals.storage.factory import open_storage
from selfevals.storage.interface import ListFilter, StorageInterface

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _case(
    *,
    feature: str = "commerce.product_resolution",
    holdout: bool = False,
    level: Level = Level.FINAL_RESPONSE,
    pii: PIIStatus = PIIStatus.RAW,
) -> EvalCase:
    return EvalCase(
        id=EvalCase.make_id(),
        workspace_id=WS,
        name="t",
        task_type="x",
        input={"messages": [{"role": "user", "content": "hi"}]},
        taxonomy=CaseTaxonomy(
            level=level,
            feature=FeatureTag(primary=feature),
            source=SourceInfo(type=DatasetSource.HANDCRAFTED),
            ground_truth=GroundTruthSpec(methods=[GroundTruthMethod.EXACT_MATCH]),
            dataset_type=DatasetType.CAPABILITY,
        ),
        expected=Expected(must_include=["pong"]),
        metadata=CaseMetadata(pii_status=pii),
        holdout=holdout,
    )


# --- manifest hash ---------------------------------------------------------


def test_manifest_hash_is_stable_and_prefixed() -> None:
    cases = [_case(), _case()]
    h1 = compute_manifest_hash(cases)
    h2 = compute_manifest_hash(cases)
    assert h1 == h2
    assert h1.startswith("sha256:")


def test_manifest_hash_invariant_to_order() -> None:
    a, b = _case(), _case()
    assert compute_manifest_hash([a, b]) == compute_manifest_hash([b, a])


def test_manifest_hash_changes_with_case_set() -> None:
    a, b = _case(), _case()
    assert compute_manifest_hash([a]) != compute_manifest_hash([a, b])


# --- statistics ------------------------------------------------------------


def test_statistics_counts_totals_and_dimensions() -> None:
    cases = [
        _case(feature="commerce.search"),
        _case(feature="commerce.search"),
        _case(feature="commerce.checkout", holdout=True),
    ]
    stats = compute_statistics(cases)
    assert stats.total_cases == 3
    assert stats.by_feature == {"commerce.search": 2, "commerce.checkout": 1}
    assert stats.holdout_count == 1
    assert stats.by_level[str(Level.FINAL_RESPONSE)] == 3
    assert stats.by_source[str(DatasetSource.HANDCRAFTED)] == 3
    assert stats.pii_breakdown[str(PIIStatus.RAW)] == 3


def test_statistics_empty() -> None:
    stats = compute_statistics([])
    assert stats.total_cases == 0
    assert stats.holdout_count == 0
    assert stats.by_feature == {}


# --- build_dataset ---------------------------------------------------------


def test_build_dataset_is_active_with_hash_and_stats() -> None:
    cases = [_case(), _case()]
    ds = build_dataset(
        workspace_id=WS,
        name="my-suite",
        dataset_type=DatasetType.CAPABILITY,
        cases=cases,
    )
    assert ds.status == DatasetStatus.ACTIVE
    assert ds.manifest_hash == compute_manifest_hash(cases)
    assert ds.statistics is not None
    assert ds.statistics.total_cases == 2
    assert {ref.id for ref in ds.cases} == {c.id for c in cases}


def test_build_dataset_respects_split_allocation() -> None:
    sa = SplitAllocation(optimization=0.5, holdout=0.5, reliability=0.0)
    ds = build_dataset(
        workspace_id=WS,
        name="s",
        dataset_type=DatasetType.CAPABILITY,
        cases=[_case()],
        split_allocation=sa,
    )
    assert ds.split_allocation.optimization == 0.5


# --- persist_dataset (standalone, no experiment) ---------------------------


def _storage(db_url: str) -> StorageInterface:
    storage = open_storage(db_url)
    with storage.open(WS) as scope:
        scope.put_entity(Workspace(id=WS, workspace_id=WS, slug=WS.lower(), name=WS))
    return storage


def test_persist_dataset_writes_cases_and_manifest(db_url: str) -> None:
    storage = _storage(db_url)
    cases = [_case(), _case()]
    try:
        with storage.open(WS) as scope:
            ds = persist_dataset(
                scope,
                name="standalone",
                dataset_type=DatasetType.CAPABILITY,
                cases=cases,
            )
        with storage.open(WS) as scope:
            stored = scope.get_entity(Dataset, ds.id)
            persisted_cases = [
                c for c in scope.list_entities(EvalCase, ListFilter()) if isinstance(c, EvalCase)
            ]
    finally:
        storage.close()

    assert isinstance(stored, Dataset)
    assert stored.status == DatasetStatus.ACTIVE
    assert len(persisted_cases) == 2
    # Standalone dataset: cases carry no experiment link.
    assert all(c.experiment_id is None for c in persisted_cases)


def test_persist_dataset_idempotent_on_fixed_id(db_url: str) -> None:
    storage = _storage(db_url)
    cases = [_case(), _case()]
    fixed = Dataset.make_id()
    try:
        with storage.open(WS) as scope:
            first = persist_dataset(
                scope,
                name="v1",
                dataset_type=DatasetType.CAPABILITY,
                cases=cases,
                dataset_id=fixed,
            )
        with storage.open(WS) as scope:
            second = persist_dataset(
                scope,
                name="v1-again",
                dataset_type=DatasetType.CAPABILITY,
                cases=cases,
                dataset_id=fixed,
            )
            all_datasets = [
                d for d in scope.list_entities(Dataset, ListFilter()) if isinstance(d, Dataset)
            ]
    finally:
        storage.close()

    assert first.id == second.id == fixed
    assert first.manifest_hash == second.manifest_hash
    # Re-importing identical content under the same id must not duplicate.
    assert len(all_datasets) == 1


def test_persist_dataset_rejects_cross_workspace_case(db_url: str) -> None:
    storage = _storage(db_url)
    foreign = _case()
    foreign.workspace_id = "ws_other"
    try:
        with storage.open(WS) as scope, pytest.raises(WorkspaceMismatchError):
            persist_dataset(
                scope,
                name="bad",
                dataset_type=DatasetType.CAPABILITY,
                cases=[foreign],
            )
    finally:
        storage.close()
