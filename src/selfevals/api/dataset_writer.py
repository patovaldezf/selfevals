"""Write path for datasets over HTTP — the three upload modes.

All three (inline JSON cases, a server-side `cases_path`, and a multipart
`.jsonl` upload) converge on `repo.datasets.persist_dataset`, the same canonical
path the CLI and the run loop use. This module turns an HTTP request into that
call and raises `DatasetWriteError` (mapped to 4xx by `app.py`) for bad input.
"""

from __future__ import annotations

from pathlib import Path

from selfevals.api.schemas import CreateDatasetRequest, DatasetDetailResponse
from selfevals.repo.datasets import (
    DatasetImportError,
    compute_manifest_hash,
    compute_statistics,
    load_cases_from_jsonl,
    load_cases_from_rows,
    persist_dataset,
)
from selfevals.runner.launch import ensure_workspace_by_id
from selfevals.schemas.dataset import Dataset, SplitAllocation
from selfevals.schemas.enums import DatasetStatus, DatasetType
from selfevals.schemas.eval_case import EvalCase
from selfevals.storage.factory import open_storage
from selfevals.storage.interface import ListFilter


class DatasetWriteError(ValueError):
    """Bad request while creating a dataset (unknown type, no cases, etc.)."""


class DatasetNotFoundError(LookupError):
    """The named dataset does not exist in the workspace (maps to 404)."""


def _resolve_type(raw: str) -> DatasetType:
    try:
        return DatasetType(raw)
    except ValueError as exc:
        raise DatasetWriteError(f"unknown dataset_type {raw!r}") from exc


def _resolve_split(raw: dict[str, float] | None) -> SplitAllocation | None:
    if raw is None:
        return None
    known = {"optimization", "holdout", "reliability"}
    kwargs: dict[str, object] = {k: v for k, v in raw.items() if k in known}
    other = {k: v for k, v in raw.items() if k not in known}
    if other:
        kwargs["other"] = other
    try:
        return SplitAllocation(**kwargs)  # type: ignore[arg-type]
    except Exception as exc:
        raise DatasetWriteError(f"invalid split_allocation: {exc}") from exc


def _persist(
    *,
    db_path: str,
    workspace_id: str,
    name: str,
    description: str | None,
    dataset_type: DatasetType,
    cases: list[EvalCase],
    split_allocation: SplitAllocation | None,
) -> DatasetDetailResponse:
    from selfevals.api.queries import dataset_detail

    storage = open_storage(db_path)
    try:
        ensure_workspace_by_id(storage, workspace_id)
        with storage.open(workspace_id) as scope:
            dataset = persist_dataset(
                scope,
                name=name,
                dataset_type=dataset_type,
                cases=cases,
                description=description,
                split_allocation=split_allocation,
            )
        detail = dataset_detail(storage, workspace_id=workspace_id, dataset_id=dataset.id)
    finally:
        storage.close()
    assert detail is not None  # just persisted it
    return detail


def create_dataset_from_request(
    *, db_path: str, workspace_id: str, body: CreateDatasetRequest
) -> DatasetDetailResponse:
    """Create a dataset from a JSON body: inline `cases` or a `cases_path`."""
    dataset_type = _resolve_type(body.dataset_type)
    split = _resolve_split(body.split_allocation)
    try:
        if body.cases is not None:
            cases = load_cases_from_rows(body.cases, workspace_id=workspace_id)
        else:
            assert body.cases_path is not None  # validator guarantees one source
            cases = load_cases_from_jsonl(Path(body.cases_path), workspace_id=workspace_id)
    except DatasetImportError as exc:
        raise DatasetWriteError(str(exc)) from exc
    return _persist(
        db_path=db_path,
        workspace_id=workspace_id,
        name=body.name,
        description=body.description,
        dataset_type=dataset_type,
        cases=cases,
        split_allocation=split,
    )


def create_dataset_from_jsonl_bytes(
    *,
    db_path: str,
    workspace_id: str,
    name: str,
    raw: bytes,
    dataset_type: str = "capability",
    description: str | None = None,
) -> DatasetDetailResponse:
    """Create a dataset from an uploaded `.jsonl` file's bytes (multipart)."""
    resolved_type = _resolve_type(dataset_type)
    rows = []
    import json

    for line_no, line in enumerate(raw.decode("utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            row = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise DatasetWriteError(f"line {line_no}: invalid JSON: {exc}") from exc
        if not isinstance(row, dict):
            raise DatasetWriteError(f"line {line_no}: expected a JSON object")
        rows.append(row)
    try:
        cases = load_cases_from_rows(rows, workspace_id=workspace_id)
    except DatasetImportError as exc:
        raise DatasetWriteError(str(exc)) from exc
    return _persist(
        db_path=db_path,
        workspace_id=workspace_id,
        name=name,
        description=description,
        dataset_type=resolved_type,
        cases=cases,
        split_allocation=None,
    )


def freeze_dataset(
    *, db_path: str, workspace_id: str, dataset_id: str
) -> DatasetDetailResponse:
    """Recompute the manifest from current cases and set status=FROZEN."""
    from selfevals.api.queries import dataset_detail

    storage = open_storage(db_path)
    try:
        with storage.open(workspace_id) as scope:
            try:
                ds = scope.get_entity(Dataset, dataset_id)
            except Exception as exc:
                raise DatasetNotFoundError(dataset_id) from exc
            assert isinstance(ds, Dataset)
            ref_ids = {ref.id for ref in ds.cases}
            cases = [
                c
                for c in scope.list_entities(EvalCase, ListFilter())
                if isinstance(c, EvalCase) and c.id in ref_ids
            ]
            if cases:
                ds.manifest_hash = compute_manifest_hash(cases)
                ds.statistics = compute_statistics(cases)
            if not ds.manifest_hash:
                raise DatasetWriteError(
                    "cannot freeze: no cases resolved and no manifest_hash present"
                )
            ds.status = DatasetStatus.FROZEN
            scope.put_entity(ds)
        detail = dataset_detail(storage, workspace_id=workspace_id, dataset_id=dataset_id)
    finally:
        storage.close()
    assert detail is not None
    return detail
