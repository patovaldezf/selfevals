"""Dataset CRUD, upload, freeze, case append, and baseline/regression checks."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile

from selfevals.api.auth import UserHeader
from selfevals.api.baseline_ops import (
    BaselineNotFoundError,
    BaselineOpError,
    get_baseline,
    run_regression_check,
    set_dataset_baseline,
)
from selfevals.api.dataset_writer import (
    DatasetNotFoundError,
    DatasetWriteError,
    append_case_to_dataset,
    create_dataset_from_jsonl_bytes,
    create_dataset_from_request,
    freeze_dataset,
)
from selfevals.api.deps import AppDeps
from selfevals.api.queries import dataset_detail, list_datasets
from selfevals.api.schemas import (
    AppendDatasetCaseRequest,
    AppendDatasetCaseResponse,
    BaselineResponse,
    CreateDatasetRequest,
    DatasetDetailResponse,
    DatasetListPage,
    RegressionCheckRequest,
    RegressionResultResponse,
    SetBaselineRequest,
)
from selfevals.schemas.enums import DatasetStatus
from selfevals.storage.interface import StorageInterface


def register(app: FastAPI, deps: AppDeps) -> None:
    @app.get(
        "/api/workspaces/{workspace_id}/datasets",
        response_model=DatasetListPage,
        tags=["datasets"],
    )
    def datasets_index(
        workspace_id: str,
        storage: StorageInterface = Depends(deps.storage),
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
        status: Annotated[
            DatasetStatus | None,
            Query(description="Filter by lifecycle status (draft/active/frozen/archived)."),
        ] = None,
        dataset_type: Annotated[
            str | None, Query(description="Filter by dataset type (e.g. capability, golden).")
        ] = None,
        _user: UserHeader = None,
    ) -> DatasetListPage:
        try:
            return list_datasets(
                storage,
                workspace_id=workspace_id,
                limit=limit,
                offset=offset,
                status=status,
                dataset_type=dataset_type,
            )
        finally:
            storage.close()

    @app.get(
        "/api/workspaces/{workspace_id}/datasets/{dataset_id}",
        response_model=DatasetDetailResponse,
        tags=["datasets"],
    )
    def datasets_show(
        workspace_id: str,
        dataset_id: str,
        storage: StorageInterface = Depends(deps.storage),
        _user: UserHeader = None,
    ) -> DatasetDetailResponse:
        try:
            detail = dataset_detail(storage, workspace_id=workspace_id, dataset_id=dataset_id)
            if detail is None:
                raise HTTPException(status_code=404, detail=f"dataset {dataset_id} not found")
            return detail
        finally:
            storage.close()

    @app.post(
        "/api/workspaces/{workspace_id}/datasets",
        response_model=DatasetDetailResponse,
        status_code=201,
        tags=["datasets"],
    )
    def datasets_create(
        workspace_id: str,
        body: CreateDatasetRequest,
        _user: UserHeader = None,
    ) -> DatasetDetailResponse:
        # Inline cases or a server-side cases_path. Persists the dataset + its
        # cases synchronously (a dataset is small; no background needed).
        try:
            return create_dataset_from_request(
                db_path=deps.storage_url, workspace_id=workspace_id, body=body
            )
        except DatasetWriteError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post(
        "/api/workspaces/{workspace_id}/datasets/upload",
        response_model=DatasetDetailResponse,
        status_code=201,
        tags=["datasets"],
    )
    def datasets_upload(
        workspace_id: str,
        name: Annotated[str, Form(description="Dataset name.")],
        file: Annotated[UploadFile, File(description="A .jsonl file, one case per line.")],
        dataset_type: Annotated[str, Form()] = "capability",
        description: Annotated[str | None, Form()] = None,
        _user: UserHeader = None,
    ) -> DatasetDetailResponse:
        # Multipart upload of a raw .jsonl — the file-drag path for a FE. Reads
        # the file with FastAPI's SpooledTemporaryFile API (no `await` needed
        # for the small-file case, keeping the handler sync so FastAPI runs it
        # in its threadpool instead of blocking the event loop on storage I/O).
        raw = file.file.read()
        try:
            return create_dataset_from_jsonl_bytes(
                db_path=deps.storage_url,
                workspace_id=workspace_id,
                name=name,
                raw=raw,
                dataset_type=dataset_type,
                description=description,
            )
        except DatasetWriteError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post(
        "/api/workspaces/{workspace_id}/datasets/{dataset_id}/freeze",
        response_model=DatasetDetailResponse,
        tags=["datasets"],
    )
    def datasets_freeze(
        workspace_id: str,
        dataset_id: str,
        _user: UserHeader = None,
    ) -> DatasetDetailResponse:
        try:
            return freeze_dataset(
                db_path=deps.storage_url, workspace_id=workspace_id, dataset_id=dataset_id
            )
        except DatasetNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"dataset {dataset_id} not found") from exc
        except DatasetWriteError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post(
        "/api/workspaces/{workspace_id}/datasets/{dataset_id}/cases",
        response_model=AppendDatasetCaseResponse,
        tags=["datasets"],
    )
    def datasets_append_case(
        workspace_id: str,
        dataset_id: str,
        body: AppendDatasetCaseRequest,
        storage: StorageInterface = Depends(deps.storage),
        _user: UserHeader = None,
    ) -> AppendDatasetCaseResponse:
        try:
            return append_case_to_dataset(
                storage,
                workspace_id=workspace_id,
                dataset_id=dataset_id,
                case_data=body.case,
                create_version_if_frozen=body.create_version_if_frozen,
            )
        except DatasetNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"dataset {dataset_id} not found") from exc
        except DatasetWriteError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            storage.close()

    @app.get(
        "/api/workspaces/{workspace_id}/datasets/{dataset_id}/baseline",
        response_model=BaselineResponse,
        tags=["baseline"],
    )
    def dataset_baseline_show(
        workspace_id: str,
        dataset_id: str,
        storage: StorageInterface = Depends(deps.storage),
        _user: UserHeader = None,
    ) -> BaselineResponse:
        try:
            return get_baseline(storage, workspace_id=workspace_id, dataset_id=dataset_id)
        except BaselineNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except BaselineOpError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            storage.close()

    @app.put(
        "/api/workspaces/{workspace_id}/datasets/{dataset_id}/baseline",
        response_model=BaselineResponse,
        tags=["baseline"],
    )
    def dataset_baseline_set(
        workspace_id: str,
        dataset_id: str,
        body: SetBaselineRequest,
        storage: StorageInterface = Depends(deps.storage),
        _user: UserHeader = None,
    ) -> BaselineResponse:
        try:
            return set_dataset_baseline(
                storage,
                workspace_id=workspace_id,
                dataset_id=dataset_id,
                iteration_id=body.iteration_id,
            )
        except BaselineOpError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            storage.close()

    @app.post(
        "/api/workspaces/{workspace_id}/datasets/{dataset_id}/regression-check",
        response_model=RegressionResultResponse,
        tags=["baseline"],
    )
    def dataset_regression_check(
        workspace_id: str,
        dataset_id: str,
        body: RegressionCheckRequest,
        storage: StorageInterface = Depends(deps.storage),
        _user: UserHeader = None,
    ) -> RegressionResultResponse:
        try:
            return run_regression_check(
                storage,
                workspace_id=workspace_id,
                dataset_id=dataset_id,
                iteration_id=body.iteration_id,
                primary_drop=body.primary_drop,
                per_class_f1_drop=body.per_class_f1_drop,
                error_rate_rise=body.error_rate_rise,
            )
        except BaselineNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except BaselineOpError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            storage.close()
