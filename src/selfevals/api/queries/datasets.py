"""Dataset listing and detail queries."""

from __future__ import annotations

from selfevals.api.queries._shared import case_summary
from selfevals.api.schemas import (
    DatasetDetailResponse,
    DatasetListPage,
    DatasetStatisticsView,
    DatasetSummary,
    SplitAllocationView,
)
from selfevals.schemas.dataset import Dataset
from selfevals.schemas.enums import DatasetStatus
from selfevals.schemas.eval_case import EvalCase
from selfevals.storage.errors import EntityNotFoundError
from selfevals.storage.interface import ListFilter, StorageInterface


def _dataset_summary(ds: Dataset) -> DatasetSummary:
    return DatasetSummary(
        id=ds.id,
        name=ds.name,
        description=ds.description,
        dataset_type=str(ds.dataset_type),
        status=str(ds.status),
        case_count=len(ds.cases),
        manifest_hash=ds.manifest_hash,
        created_at=ds.created_at,
        updated_at=ds.updated_at,
    )


def list_datasets(
    storage: StorageInterface,
    *,
    workspace_id: str,
    limit: int = 100,
    offset: int = 0,
    status: DatasetStatus | None = None,
    dataset_type: str | None = None,
) -> DatasetListPage:
    """Paginated datasets listing, with optional status/type filters.

    Filters apply in memory before pagination so `total`/`has_more` describe
    the filtered set (same approach as `list_experiments`). Newest first.
    """
    with storage.open(workspace_id) as scope:
        datasets = [
            d
            for d in scope.list_entities(Dataset, ListFilter(order_by="created_at"))
            if isinstance(d, Dataset)
        ]
    if status is not None:
        datasets = [d for d in datasets if d.status == status]
    if dataset_type is not None:
        datasets = [d for d in datasets if str(d.dataset_type) == dataset_type]
    total = len(datasets)
    page = datasets[offset : offset + limit]
    return DatasetListPage(
        items=[_dataset_summary(d) for d in page],
        total=total,
        limit=limit,
        offset=offset,
        has_more=(offset + limit) < total,
    )


def dataset_detail(
    storage: StorageInterface, *, workspace_id: str, dataset_id: str
) -> DatasetDetailResponse | None:
    """One dataset with its resolved cases, split, and statistics.

    Returns None (the endpoint maps to 404) when the dataset is missing. Cases
    are resolved from the dataset's `EntityRef` list; refs whose EvalCase is no
    longer in storage are skipped rather than failing the whole response.
    """
    with storage.open(workspace_id) as scope:
        try:
            ds = scope.get_entity(Dataset, dataset_id)
        except EntityNotFoundError:
            return None
        assert isinstance(ds, Dataset)
        ref_ids = {ref.id for ref in ds.cases}
        cases = [
            c
            for c in scope.list_entities(EvalCase, ListFilter())
            if isinstance(c, EvalCase) and c.id in ref_ids
        ]
    cases.sort(key=lambda c: c.name)
    sa = ds.split_allocation
    stats = (
        DatasetStatisticsView(**ds.statistics.model_dump(mode="json"))
        if ds.statistics is not None
        else None
    )
    return DatasetDetailResponse(
        id=ds.id,
        name=ds.name,
        description=ds.description,
        dataset_type=str(ds.dataset_type),
        status=str(ds.status),
        case_count=len(ds.cases),
        manifest_hash=ds.manifest_hash,
        split_allocation=SplitAllocationView(
            optimization=sa.optimization,
            holdout=sa.holdout,
            reliability=sa.reliability,
            other=dict(sa.other),
        ),
        statistics=stats,
        cases=[case_summary(c) for c in cases],
        created_at=ds.created_at,
        updated_at=ds.updated_at,
    )
