"""Pairwise verdicts (LLM + human) and tournaments (Elo / Bradley-Terry)."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query

from selfevals.api.auth import UserHeader
from selfevals.api.deps import AppDeps
from selfevals.api.pairwise_ops import (
    PairwiseApiError,
    get_pairwise_calibration,
    ingest_pairwise_verdicts,
    list_pairwise_tournaments,
    list_pairwise_verdicts,
    run_pairwise_tournament,
)
from selfevals.api.schemas import (
    IngestPairwiseRequest,
    PairwiseCalibrationResponse,
    PairwiseIngestSummaryResponse,
    PairwiseVerdictResponse,
    RunTournamentRequest,
    TournamentResponse,
)
from selfevals.storage.interface import StorageInterface


def register(app: FastAPI, deps: AppDeps) -> None:
    @app.post(
        "/api/workspaces/{workspace_id}/experiments/{experiment_id}/verdicts/ingest",
        response_model=PairwiseIngestSummaryResponse,
        tags=["pairwise"],
    )
    def verdicts_ingest(
        workspace_id: str,
        experiment_id: str,
        body: IngestPairwiseRequest,
        storage: StorageInterface = Depends(deps.storage),
        _user: UserHeader = None,
    ) -> PairwiseIngestSummaryResponse:
        try:
            return ingest_pairwise_verdicts(
                storage,
                workspace_id=workspace_id,
                experiment_id=experiment_id,
                verdicts=body.verdicts,
            )
        except PairwiseApiError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            storage.close()

    @app.get(
        "/api/workspaces/{workspace_id}/experiments/{experiment_id}/verdicts",
        response_model=list[PairwiseVerdictResponse],
        tags=["pairwise"],
    )
    def verdicts_list(
        workspace_id: str,
        experiment_id: str,
        storage: StorageInterface = Depends(deps.storage),
        case_id: str | None = None,
        judge_kind: Annotated[str | None, Query(pattern="^(llm|human)$")] = None,
        _user: UserHeader = None,
    ) -> list[PairwiseVerdictResponse]:
        try:
            return list_pairwise_verdicts(
                storage,
                workspace_id=workspace_id,
                experiment_id=experiment_id,
                case_id=case_id,
                judge_kind=judge_kind,
            )
        finally:
            storage.close()

    @app.get(
        "/api/workspaces/{workspace_id}/experiments/{experiment_id}/verdicts/calibration",
        response_model=PairwiseCalibrationResponse,
        tags=["pairwise"],
    )
    def verdicts_calibration(
        workspace_id: str,
        experiment_id: str,
        storage: StorageInterface = Depends(deps.storage),
        _user: UserHeader = None,
    ) -> PairwiseCalibrationResponse:
        try:
            return get_pairwise_calibration(
                storage, workspace_id=workspace_id, experiment_id=experiment_id
            )
        finally:
            storage.close()

    @app.post(
        "/api/workspaces/{workspace_id}/experiments/{experiment_id}/tournaments",
        response_model=TournamentResponse,
        tags=["pairwise"],
    )
    def tournament_run(
        workspace_id: str,
        experiment_id: str,
        body: RunTournamentRequest,
        storage: StorageInterface = Depends(deps.storage),
        _user: UserHeader = None,
    ) -> TournamentResponse:
        try:
            return run_pairwise_tournament(
                storage,
                workspace_id=workspace_id,
                experiment_id=experiment_id,
                request=body,
            )
        except PairwiseApiError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            storage.close()

    @app.get(
        "/api/workspaces/{workspace_id}/experiments/{experiment_id}/tournaments",
        response_model=list[TournamentResponse],
        tags=["pairwise"],
    )
    def tournaments_list(
        workspace_id: str,
        experiment_id: str,
        storage: StorageInterface = Depends(deps.storage),
        _user: UserHeader = None,
    ) -> list[TournamentResponse]:
        try:
            return list_pairwise_tournaments(
                storage, workspace_id=workspace_id, experiment_id=experiment_id
            )
        finally:
            storage.close()
