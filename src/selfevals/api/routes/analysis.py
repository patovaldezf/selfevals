"""Error-analysis bundle export and human-coded result ingest (loop-closer 2C)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query

from selfevals.api.auth import UserHeader
from selfevals.api.deps import AppDeps
from selfevals.api.schemas import AnalysisIngestSummaryResponse
from selfevals.storage.interface import StorageInterface


def register(app: FastAPI, deps: AppDeps) -> None:
    @app.get(
        "/api/workspaces/{workspace_id}/experiments/{experiment_id}/analysis/bundle",
        tags=["analysis"],
    )
    def analysis_bundle(
        workspace_id: str,
        experiment_id: str,
        storage: StorageInterface = Depends(deps.storage),
        iteration: Annotated[int | None, Query(ge=0)] = None,
        all: Annotated[
            bool, Query(description="Include passing traces, not just failures.")
        ] = False,
        _user: UserHeader = None,
    ) -> dict[str, Any]:
        # The bundle is the analysis/schemas.py AnalysisBundle, passed through
        # as JSON so the contract lives in one place.
        from selfevals.analysis import build_bundle

        try:
            bundle = build_bundle(
                storage,
                workspace_id=workspace_id,
                experiment_id=experiment_id,
                iteration=iteration,
                only_failed=not all,
            )
            return bundle.model_dump(mode="json")
        finally:
            storage.close()

    @app.post(
        "/api/workspaces/{workspace_id}/experiments/{experiment_id}/analysis/ingest",
        response_model=AnalysisIngestSummaryResponse,
        tags=["analysis"],
    )
    def analysis_ingest(
        workspace_id: str,
        experiment_id: str,
        body: dict[str, Any],
        storage: StorageInterface = Depends(deps.storage),
        user: UserHeader = None,
    ) -> AnalysisIngestSummaryResponse:
        # The body is an analysis/schemas.py AnalysisResult; validate it here so
        # the contract stays defined in the domain model, not duplicated.
        from selfevals.analysis import ingest_result
        from selfevals.analysis.ingest import AnalysisIngestError
        from selfevals.analysis.schemas import AnalysisResult

        try:
            result = AnalysisResult.model_validate(body)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"invalid AnalysisResult: {exc}") from exc
        try:
            summary = ingest_result(
                storage,
                workspace_id=workspace_id,
                experiment_id=experiment_id,
                result=result,
                proposed_by=f"human:{user or 'local'}",
                object_store=deps.object_store,
            )
            return AnalysisIngestSummaryResponse(
                assignments_applied=summary.assignments_applied,
                created_candidates=summary.created_candidates,
                updated_candidates=summary.updated_candidates,
                hypotheses_recorded=summary.hypotheses_recorded,
            )
        except AnalysisIngestError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            storage.close()
