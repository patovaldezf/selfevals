from __future__ import annotations

import pytest
from pydantic import ValidationError

from selfeval.schemas._base import EntityRef
from selfeval.schemas.dataset import Dataset, DatasetStatistics, SplitAllocation
from selfeval.schemas.enums import DatasetStatus, DatasetType

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _dataset(**overrides: object) -> Dataset:
    base: dict[str, object] = {
        "id": Dataset.make_id(),
        "workspace_id": WS,
        "name": "smoke-suite",
        "dataset_type": DatasetType.SMOKE,
        "cases": [EntityRef(id="ec_01HZZZZZZZZZZZZZZZZZZZZZZZ", version=1)],
    }
    base.update(overrides)
    return Dataset(**base)  # type: ignore[arg-type]


def test_dataset_draft_no_manifest_hash_required() -> None:
    ds = _dataset()
    assert ds.status == DatasetStatus.DRAFT


def test_dataset_frozen_requires_manifest_hash() -> None:
    with pytest.raises(ValidationError):
        _dataset(status=DatasetStatus.FROZEN)


def test_dataset_active_requires_manifest_hash() -> None:
    with pytest.raises(ValidationError):
        _dataset(status=DatasetStatus.ACTIVE)


def test_dataset_with_manifest_hash_can_be_frozen() -> None:
    ds = _dataset(manifest_hash="sha256:abc", status=DatasetStatus.FROZEN)
    assert ds.status == DatasetStatus.FROZEN


def test_duplicate_cases_rejected() -> None:
    ref = EntityRef(id="ec_01HZZZZZZZZZZZZZZZZZZZZZZZ")
    with pytest.raises(ValidationError):
        _dataset(cases=[ref, ref])


def test_split_allocation_must_sum_to_one() -> None:
    with pytest.raises(ValidationError):
        SplitAllocation(optimization=0.5, holdout=0.2, reliability=0.1)


def test_split_allocation_with_other() -> None:
    sa = SplitAllocation(optimization=0.5, holdout=0.2, reliability=0.1, other={"x": 0.2})
    assert sa.optimization == 0.5


def test_regression_frozen_immutability_blocks_cases_mutation() -> None:
    ds = _dataset(
        dataset_type=DatasetType.REGRESSION,
        manifest_hash="sha256:abc",
        status=DatasetStatus.FROZEN,
    )
    with pytest.raises(ValueError, match="cannot mutate 'cases'"):
        ds.cases = [EntityRef(id="ec_other")]


def test_regression_frozen_immutability_blocks_type_change() -> None:
    ds = _dataset(
        dataset_type=DatasetType.REGRESSION,
        manifest_hash="sha256:abc",
        status=DatasetStatus.FROZEN,
    )
    with pytest.raises(ValueError, match="cannot mutate 'dataset_type'"):
        ds.dataset_type = DatasetType.CAPABILITY


def test_non_regression_frozen_can_still_swap_cases() -> None:
    ds = _dataset(
        dataset_type=DatasetType.CAPABILITY,
        manifest_hash="sha256:abc",
        status=DatasetStatus.FROZEN,
    )
    ds.cases = [EntityRef(id="ec_new")]
    assert ds.cases[0].id == "ec_new"


def test_statistics_cache_invalidated_on_case_mutation() -> None:
    ds = _dataset(
        dataset_type=DatasetType.CAPABILITY,
        statistics=DatasetStatistics(total_cases=1),
    )
    assert ds.statistics is not None
    ds.cases = [EntityRef(id="ec_new")]
    assert ds.statistics is None


def test_status_mutation_allowed_even_when_frozen() -> None:
    ds = _dataset(
        dataset_type=DatasetType.REGRESSION,
        manifest_hash="sha256:abc",
        status=DatasetStatus.FROZEN,
    )
    ds.status = DatasetStatus.ARCHIVED  # lifecycle field — must remain mutable
    assert ds.status == DatasetStatus.ARCHIVED
