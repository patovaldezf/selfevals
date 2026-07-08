"""Experiment lifecycle: list, launch, cancel, results, and iteration drill-down."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query

from selfevals.api.auth import UserHeader
from selfevals.api.deps import AppDeps
from selfevals.api.queries import (
    experiment_cases,
    experiment_decisions,
    experiment_detail,
    experiment_iterations,
    experiment_results,
    iteration_detail,
    list_experiments,
    load_compare,
    load_iteration_funnel,
)
from selfevals.api.run_jobs import request_cancel_run_job
from selfevals.api.run_launcher import launch_experiment_run
from selfevals.api.schemas import (
    CaseListResponse,
    CompareResponse,
    DecisionRecordResponse,
    ExperimentDetailResponse,
    ExperimentListPage,
    ExperimentResultsResponse,
    FunnelResponse,
    IterationListResponse,
    RunExperimentRequest,
    RunExperimentResponse,
)
from selfevals.schemas.enums import ExperimentState
from selfevals.storage.interface import StorageInterface


def register(app: FastAPI, deps: AppDeps) -> None:
    @app.get(
        "/api/workspaces/{workspace_id}/experiments",
        response_model=ExperimentListPage,
        tags=["experiments"],
    )
    def experiments_index(
        workspace_id: str,
        storage: StorageInterface = Depends(deps.storage),
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
        state: Annotated[
            ExperimentState | None,
            Query(description="Filter by experiment state (e.g. running, completed)."),
        ] = None,
        feature: Annotated[
            str | None,
            Query(description="Filter to experiments whose taxonomy.target_features contains this."),
        ] = None,
        _user: UserHeader = None,
    ) -> ExperimentListPage:
        try:
            return list_experiments(
                storage,
                workspace_id=workspace_id,
                limit=limit,
                offset=offset,
                state=state,
                feature=feature,
            )
        finally:
            storage.close()

    @app.post(
        "/api/workspaces/{workspace_id}/experiments/run",
        response_model=RunExperimentResponse,
        status_code=202,
        tags=["experiments"],
    )
    def experiments_run(
        workspace_id: str,
        body: RunExperimentRequest,
        _user: UserHeader = None,
    ) -> RunExperimentResponse:
        # Non-blocking: validates + persists synchronously, then runs the loop
        # on a daemon thread. Returns 202 immediately; the FE polls the
        # experiment detail (state climbs to completed/aborted).
        return launch_experiment_run(
            storage_url=deps.storage_url,
            workspace_id=workspace_id,
            body=body,
        )

    @app.post(
        "/api/workspaces/{workspace_id}/experiments/{experiment_id}/cancel",
        response_model=RunExperimentResponse,
        status_code=202,
        tags=["experiments"],
    )
    def experiments_cancel(
        workspace_id: str,
        experiment_id: str,
        storage: StorageInterface = Depends(deps.storage),
        _user: UserHeader = None,
    ) -> RunExperimentResponse:
        try:
            job = request_cancel_run_job(
                storage,
                workspace_id=workspace_id,
                experiment_id=experiment_id,
            )
            if job is None:
                raise HTTPException(status_code=404, detail="run job not found")
            return RunExperimentResponse(
                experiment_id=experiment_id,
                workspace_id=workspace_id,
                state=str(job.status),
                job_id=job.id,
            )
        finally:
            storage.close()

    @app.get(
        "/api/workspaces/{workspace_id}/experiments/{experiment_id}",
        response_model=ExperimentDetailResponse,
        tags=["experiments"],
    )
    def experiments_show(
        workspace_id: str,
        experiment_id: str,
        storage: StorageInterface = Depends(deps.storage),
        _user: UserHeader = None,
    ) -> ExperimentDetailResponse:
        try:
            detail = experiment_detail(
                storage,
                workspace_id=workspace_id,
                experiment_id=experiment_id,
            )
            if detail is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"experiment {experiment_id} not found",
                )
            return detail
        finally:
            storage.close()

    @app.get(
        "/api/workspaces/{workspace_id}/experiments/{experiment_id}/iterations",
        response_model=IterationListResponse,
        tags=["experiments"],
    )
    def experiments_iterations(
        workspace_id: str,
        experiment_id: str,
        storage: StorageInterface = Depends(deps.storage),
        _user: UserHeader = None,
    ) -> IterationListResponse:
        try:
            return IterationListResponse(
                iterations=experiment_iterations(
                    storage,
                    workspace_id=workspace_id,
                    experiment_id=experiment_id,
                )
            )
        finally:
            storage.close()

    @app.get(
        "/api/workspaces/{workspace_id}/experiments/{experiment_id}/cases",
        response_model=CaseListResponse,
        tags=["experiments"],
    )
    def experiments_cases(
        workspace_id: str,
        experiment_id: str,
        storage: StorageInterface = Depends(deps.storage),
        _user: UserHeader = None,
    ) -> CaseListResponse:
        # The eval cases a run executed, persisted at launch. Holdout cases are
        # included and flagged. Empty list (not 404) for an experiment that
        # predates case persistence or has no cases yet — the FE renders an
        # honest empty state rather than an error.
        try:
            return experiment_cases(
                storage,
                workspace_id=workspace_id,
                experiment_id=experiment_id,
            )
        finally:
            storage.close()

    @app.get(
        "/api/workspaces/{workspace_id}/experiments/{experiment_id}/results",
        response_model=ExperimentResultsResponse,
        # Each scenario's expected/detected only carries the dimensions the case
        # declared; exclude_none keeps the JSON compact (no null rules) at scale.
        response_model_exclude_none=True,
        tags=["experiments"],
        summary="Per-scenario expected vs detected vs matched (best iteration)",
        description=(
            "Returns one `ScenarioResult` per case of the best iteration. "
            "`expected`/`detected` are **derived per declared dimension**: a case "
            "that declares `structured_output` gets only that; a `must_include` "
            "case gets substrings + the produced `content` (+ `missing` on a gap); "
            "a tool case gets `required_tools` vs `tools_invoked`. Undeclared "
            "dimensions are omitted (not null), so the payload stays compact. "
            "`message` is the classified reply. A conversation case carries its "
            "per-turn breakdown in `turns[]` (same shape) when called with "
            "`?include=turns`.\n\n"
            "**Migration (0.8.0 → 0.9.0, breaking):** the old flat `CaseResultRow` "
            "(with fixed `detected={content,structured_output,tools_invoked}`) is "
            "replaced by this recursive, dimension-derived `ScenarioResult`."
        ),
    )
    def experiments_results(
        workspace_id: str,
        experiment_id: str,
        include: Annotated[
            str | None,
            Query(
                description=(
                    "Comma-separated expansions. `turns` expands each conversation "
                    "case into per-turn `ScenarioResult`s (off by default — the "
                    "case-level grid stays one representative trace per case)."
                ),
            ),
        ] = None,
        storage: StorageInterface = Depends(deps.storage),
        _user: UserHeader = None,
    ) -> ExperimentResultsResponse:
        # Per-scenario expected/detected/matched for the best iteration. Cases
        # whose traces weren't persisted are listed with detected/matched null
        # rather than dropped, so the grid is honest.
        include_set = {p.strip() for p in (include or "").split(",") if p.strip()}
        try:
            results = experiment_results(
                storage,
                workspace_id=workspace_id,
                experiment_id=experiment_id,
                include_turns="turns" in include_set,
            )
            if results is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"experiment {experiment_id} not found",
                )
            return results
        finally:
            storage.close()

    @app.get(
        "/api/workspaces/{workspace_id}/experiments/{experiment_id}/decisions",
        response_model=list[DecisionRecordResponse],
        tags=["experiments"],
    )
    def experiments_decisions(
        workspace_id: str,
        experiment_id: str,
        storage: StorageInterface = Depends(deps.storage),
        _user: UserHeader = None,
    ) -> list[DecisionRecordResponse]:
        try:
            return [
                DecisionRecordResponse(**d)
                for d in experiment_decisions(
                    storage,
                    workspace_id=workspace_id,
                    experiment_id=experiment_id,
                )
            ]
        finally:
            storage.close()

    @app.get(
        "/api/workspaces/{workspace_id}/experiments/{experiment_id}/compare",
        response_model=CompareResponse,
        tags=["experiments"],
    )
    def experiments_compare(
        workspace_id: str,
        experiment_id: str,
        a: Annotated[str, Query(description="Iteration A record id.")],
        b: Annotated[str, Query(description="Iteration B record id.")],
        storage: StorageInterface = Depends(deps.storage),
        _user: UserHeader = None,
    ) -> CompareResponse:
        try:
            try:
                result = load_compare(
                    storage,
                    workspace_id=workspace_id,
                    experiment_id=experiment_id,
                    a_id=a,
                    b_id=b,
                )
            except ValueError as exc:
                # Cross-experiment ids — not an apples-to-apples comparison.
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if result is None:
                raise HTTPException(
                    status_code=404,
                    detail="one or both iterations not found",
                )
            return result
        finally:
            storage.close()

    @app.get(
        "/api/workspaces/{workspace_id}/iterations/{iteration_id}",
        tags=["experiments"],
        responses={
            200: {
                "description": (
                    "Full iteration drill-down: `{iteration: IterationRecord, "
                    "decision: DecisionRecord | null}`, each a JSON dump of the "
                    "canonical domain model. Returned untyped on purpose — the FE "
                    "renders it generically rather than against a view schema."
                ),
            },
            404: {"description": "Iteration not found."},
        },
    )
    def iterations_show(
        workspace_id: str,
        iteration_id: str,
        storage: StorageInterface = Depends(deps.storage),
        _user: UserHeader = None,
    ) -> dict[str, Any]:
        try:
            detail = iteration_detail(
                storage,
                workspace_id=workspace_id,
                iteration_id=iteration_id,
            )
            if detail is None:
                raise HTTPException(status_code=404, detail="iteration not found")
            return detail
        finally:
            storage.close()

    @app.get(
        "/api/workspaces/{workspace_id}/iterations/{iteration_id}/funnel",
        response_model=FunnelResponse,
        tags=["experiments"],
    )
    def iterations_funnel(
        workspace_id: str,
        iteration_id: str,
        storage: StorageInterface = Depends(deps.storage),
        _user: UserHeader = None,
    ) -> FunnelResponse:
        try:
            funnel = load_iteration_funnel(
                storage,
                workspace_id=workspace_id,
                iteration_id=iteration_id,
            )
            if funnel is None:
                raise HTTPException(status_code=404, detail="iteration not found")
            return funnel
        finally:
            storage.close()
