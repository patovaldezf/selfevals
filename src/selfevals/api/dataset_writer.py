"""Write path for datasets over HTTP — the three upload modes.

All three (inline JSON cases, a server-side `cases_path`, and a multipart
`.jsonl` upload) converge on `repo.datasets.persist_dataset`, the same canonical
path the CLI and the run loop use. This module turns an HTTP request into that
call and raises `DatasetWriteError` (mapped to 4xx by `app.py`) for bad input.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from selfevals._internal.time import utc_now
from selfevals.api.schemas import (
    AppendDatasetCaseResponse,
    CreateDatasetRequest,
    DatasetDetailResponse,
    PromoteCaseDraftRequest,
    PromoteCaseDraftResponse,
)
from selfevals.repo.datasets import (
    DatasetImportError,
    compute_manifest_hash,
    compute_statistics,
    load_cases_from_jsonl,
    load_cases_from_rows,
    persist_dataset,
)
from selfevals.runner.launch import ensure_workspace_by_id
from selfevals.schemas._base import EntityRef
from selfevals.schemas.dataset import Dataset, SplitAllocation
from selfevals.schemas.enums import DatasetSource, DatasetStatus, DatasetType
from selfevals.schemas.eval_case import EvalCase, SourceInfo
from selfevals.schemas.trace import Trace
from selfevals.storage.errors import EntityNotFoundError
from selfevals.storage.factory import open_storage
from selfevals.storage.interface import ListFilter, StorageInterface, WorkspaceScope


class DatasetWriteError(ValueError):
    """Bad request while creating a dataset (unknown type, no cases, etc.)."""


class DatasetNotFoundError(LookupError):
    """The named dataset does not exist in the workspace (maps to 404)."""


class TracePromotionError(ValueError):
    """A trace cannot be converted into a regression case draft."""


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


def _load_trace(scope: WorkspaceScope, trace_id: str) -> Trace:
    try:
        trace = scope.get_entity(Trace, trace_id)
        assert isinstance(trace, Trace)
        return trace
    except EntityNotFoundError:
        matches = scope.list_entities(
            Trace, ListFilter(where={"run.run_id": trace_id}, limit=1)
        )
        if not matches:
            raise
        trace = matches[0]
        assert isinstance(trace, Trace)
        return trace


def _cases_for_dataset(scope: WorkspaceScope, dataset: Dataset) -> list[EvalCase]:
    cases: list[EvalCase] = []
    for ref in dataset.cases:
        case = scope.get_entity(EvalCase, ref.id)
        assert isinstance(case, EvalCase)
        cases.append(case)
    return cases


def draft_regression_case_from_trace(
    storage: StorageInterface,
    *,
    workspace_id: str,
    trace_id: str,
    body: PromoteCaseDraftRequest | None = None,
) -> PromoteCaseDraftResponse:
    """Build an editable regression EvalCase draft from a persisted trace."""
    body = body or PromoteCaseDraftRequest()
    with storage.open(workspace_id) as scope:
        try:
            trace = _load_trace(scope, trace_id)
        except EntityNotFoundError as exc:
            raise TracePromotionError(f"trace {trace_id} not found") from exc
        if trace.run.eval_case_id is None:
            raise TracePromotionError("trace has no eval_case_id; cannot find source case")
        try:
            source = scope.get_entity(EvalCase, trace.run.eval_case_id)
        except EntityNotFoundError as exc:
            raise TracePromotionError(
                f"source case {trace.run.eval_case_id} not found"
            ) from exc
        assert isinstance(source, EvalCase)

    case_data = source.model_dump(mode="json")
    case_data["id"] = EvalCase.make_id()
    case_data["version"] = 1
    case_data["created_at"] = utc_now().isoformat()
    case_data["updated_at"] = utc_now().isoformat()
    case_data["deleted_at"] = None
    case_data["experiment_id"] = None
    case_data["name"] = body.name or f"Regression: {source.name}"
    case_data["taxonomy"]["source"] = SourceInfo(
        type=DatasetSource.FAILURE,
        failure_id=trace.id,
        parent_case_id=source.id,
    ).model_dump(mode="json")
    case_data["taxonomy"]["dataset_type"] = DatasetType.REGRESSION.value
    notes = body.notes or ""
    provenance = (
        f"Promoted from trace {trace.id} / run {trace.run.run_id}; "
        f"source case {source.id}."
    )
    case_data["metadata"]["notes"] = f"{notes}\n{provenance}".strip()
    case_data["content_hash"] = None
    draft = EvalCase.model_validate(case_data)
    warnings: list[str] = []
    if not trace.grader_results:
        warnings.append("trace has no grader results; expected was copied from source case")
    return PromoteCaseDraftResponse(
        case=draft.model_dump(mode="json"),
        source_trace_id=trace.id,
        source_run_id=trace.run.run_id,
        source_case_id=source.id,
        warnings=warnings,
    )


def append_case_to_dataset(
    storage: StorageInterface,
    *,
    workspace_id: str,
    dataset_id: str,
    case_data: dict[str, Any],
    create_version_if_frozen: bool = True,
) -> AppendDatasetCaseResponse:
    """Append a confirmed EvalCase to a regression dataset."""
    from selfevals.api.queries import dataset_detail

    case_payload = dict(case_data)
    case_payload.setdefault("id", EvalCase.make_id())
    case_payload.setdefault("workspace_id", workspace_id)
    try:
        case = EvalCase.model_validate(case_payload)
    except Exception as exc:
        raise DatasetWriteError(f"invalid case: {exc}") from exc
    if case.workspace_id != workspace_id:
        raise DatasetWriteError("case workspace_id does not match route workspace")
    if case.taxonomy.dataset_type != DatasetType.REGRESSION:
        raise DatasetWriteError("promoted cases must have taxonomy.dataset_type=regression")

    storage_for_detail = storage
    with storage.open(workspace_id) as scope:
        try:
            dataset = scope.get_entity(Dataset, dataset_id)
        except EntityNotFoundError as exc:
            raise DatasetNotFoundError(dataset_id) from exc
        assert isinstance(dataset, Dataset)
        if dataset.dataset_type != DatasetType.REGRESSION:
            raise DatasetWriteError("target dataset must have dataset_type=regression")
        existing = _cases_for_dataset(scope, dataset)
        if dataset.status == DatasetStatus.FROZEN:
            if not create_version_if_frozen:
                raise DatasetWriteError(
                    "target regression dataset is frozen; create a new version"
                )
            new_dataset = persist_dataset(
                scope,
                name=f"{dataset.name} + regression",
                dataset_type=DatasetType.REGRESSION,
                cases=[*existing, case],
                description=dataset.description,
                split_allocation=dataset.split_allocation,
                status=DatasetStatus.ACTIVE,
                source_dataset_id=dataset.id,
            )
            target_id = new_dataset.id
            created_new = True
        else:
            scope.put_entity(case)
            dataset.cases = [*dataset.cases, EntityRef(id=case.id, version=case.version)]
            updated_cases = [*existing, case]
            dataset.manifest_hash = compute_manifest_hash(updated_cases)
            dataset.statistics = compute_statistics(updated_cases)
            scope.put_entity(dataset)
            target_id = dataset.id
            created_new = False

    detail = dataset_detail(storage_for_detail, workspace_id=workspace_id, dataset_id=target_id)
    assert detail is not None
    return AppendDatasetCaseResponse(
        dataset=detail,
        case_id=case.id,
        created_new_dataset=created_new,
    )
