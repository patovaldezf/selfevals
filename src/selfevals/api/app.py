"""FastAPI app — read-mostly HTTP bridge over configured storage.

Mounted on `/` (no version prefix; this is a single internal service).
Endpoints map 1:1 to the pages of the web UI; payload shapes match
the existing Pydantic models so the web side can validate against the
same canonical JSON.

Auth: stubbed via a single `X-SelfEvals-User` header (default
`"local"`). Real auth lands later; everything else is forward-compat.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Any

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse

from selfevals.api.broker import get_broker
from selfevals.api.dataset_writer import (
    DatasetNotFoundError,
    DatasetWriteError,
    create_dataset_from_jsonl_bytes,
    create_dataset_from_request,
    freeze_dataset,
)
from selfevals.api.metrics import (
    cost_metrics,
    failure_mode_metrics,
    latency_metrics,
    pass_rate_metrics,
    token_metrics,
    tool_metrics,
)
from selfevals.api.queries import (
    AnchorPoint,
    anchor_set_history,
    dataset_detail,
    experiment_cases,
    experiment_decisions,
    experiment_detail,
    experiment_iterations,
    experiment_results,
    iteration_detail,
    list_datasets,
    list_experiments,
    list_workspaces,
    load_compare,
    load_iteration_funnel,
    load_thread,
    load_trace,
    workspace_detail,
)
from selfevals.api.run_jobs import request_cancel_run_job
from selfevals.api.run_launcher import launch_experiment_run
from selfevals.api.schemas import (
    ActiveRun,
    ActiveRunsResponse,
    CaseListResponse,
    CompareResponse,
    CostMetricsResponse,
    CreateDatasetRequest,
    CreateWorkspaceRequest,
    DatasetDetailResponse,
    DatasetListPage,
    DecisionRecordResponse,
    ExperimentDetailResponse,
    ExperimentListPage,
    ExperimentResultsResponse,
    FailureModeMetricsResponse,
    FunnelResponse,
    HealthResponse,
    IterationListResponse,
    LatencyMetricsResponse,
    PassRateMetricsResponse,
    RunExperimentRequest,
    RunExperimentResponse,
    ThreadResponse,
    TokenMetricsResponse,
    ToolMetricsResponse,
    TraceResponse,
    WorkspaceListResponse,
    WorkspaceResponse,
)
from selfevals.api.sse import stream_trace
from selfevals.schemas.enums import DatasetStatus, ExperimentState
from selfevals.storage.errors import ObjectNotFoundError, PointerHashMismatchError
from selfevals.storage.factory import (
    object_store_base_for_storage_url,
    open_storage,
    resolve_storage_url,
    storage_url_label,
)
from selfevals.storage.filesystem import FilesystemObjectStore, parse_pointer
from selfevals.storage.interface import StorageInterface

_USER_HEADER = "X-SelfEvals-User"

# Dev frontends that may call the API cross-origin. 5173 is the bundled
# SvelteKit web UI; 3000 is the common Next/Vite default (e.g. the seals
# playground). Override with SELFEVALS_CORS_ORIGINS (comma-separated) to add a
# tunnel/deploy origin without code changes.
_DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
)


def _cors_origins() -> list[str]:
    raw = os.environ.get("SELFEVALS_CORS_ORIGINS")
    if raw:
        # Explicit override wins outright; trim blanks and empties.
        return [o.strip() for o in raw.split(",") if o.strip()]
    return list(_DEFAULT_CORS_ORIGINS)

UserHeader = Annotated[
    str | None,
    Header(alias=_USER_HEADER, description="Stubbed user id (auth is post-MVP)."),
]


def build_app(*, db_path: str | None = None) -> FastAPI:
    """Construct the FastAPI app, parameterized on the storage URL."""
    resolved = resolve_storage_url(db_path)
    # Filesystem object store (SELFEVALS_OBJECTS_DIR, default ./objects) for
    # large trace payloads. The store is process-local and cheap to construct,
    # so we build one app-wide instance rather than per-request.
    object_store = FilesystemObjectStore(object_store_base_for_storage_url(resolved))

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # Capture the running event loop so the OTLP receiver thread
        # (which runs sync) can schedule span publishes onto it.
        get_broker().bind_loop(asyncio.get_running_loop())
        yield

    app = FastAPI(
        title="selfevals",
        description="HTTP bridge for the selfevals evals framework.",
        version="0.0.1",
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    def _storage() -> Iterator[StorageInterface]:
        # `yield` dependency: FastAPI closes the connection after the response,
        # so a handler (or a query helper it calls) can never leak a psycopg
        # connection even if it forgets an explicit close. Handlers may still
        # call ``storage.close()`` early — psycopg's close() is idempotent.
        store = open_storage(resolved)
        try:
            yield store
        finally:
            store.close()

    def _storage_factory() -> StorageInterface:
        return open_storage(resolved)

    @app.get("/api/health", response_model=HealthResponse, tags=["meta"])
    def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            db_path=storage_url_label(resolved),
            storage_url=storage_url_label(resolved),
            storage_backend="postgres",
        )

    @app.get(
        "/api/workspaces",
        response_model=WorkspaceListResponse,
        tags=["workspaces"],
    )
    def workspaces_index(
        storage: StorageInterface = Depends(_storage),
        _user: UserHeader = None,
    ) -> WorkspaceListResponse:
        try:
            return WorkspaceListResponse(workspaces=list_workspaces(storage))
        finally:
            storage.close()

    @app.get(
        "/api/workspaces/{workspace_id}",
        response_model=WorkspaceResponse,
        tags=["workspaces"],
    )
    def workspaces_show(
        workspace_id: str,
        storage: StorageInterface = Depends(_storage),
        _user: UserHeader = None,
    ) -> WorkspaceResponse:
        try:
            ws = workspace_detail(storage, workspace_id=workspace_id)
            if ws is None:
                raise HTTPException(status_code=404, detail="workspace not found")
            return ws
        finally:
            storage.close()

    @app.post(
        "/api/workspaces",
        response_model=WorkspaceResponse,
        status_code=201,
        tags=["workspaces"],
    )
    def workspaces_create(
        body: CreateWorkspaceRequest,
        storage: StorageInterface = Depends(_storage),
        user: UserHeader = None,
    ) -> WorkspaceResponse:
        from selfevals.storage.seed import seed_workspace

        try:
            seeded = seed_workspace(
                storage,
                slug=body.slug,
                name=body.name or body.slug,
                user_id=user or "local",
                description=body.description,
            )
            ws = seeded.workspace
            return WorkspaceResponse(
                id=ws.id,
                slug=ws.slug,
                name=ws.name,
                description=ws.description,
                owner_id=ws.owner_id,
                created_at=ws.created_at,
                experiment_count=0,
                recent_health=None,
            )
        finally:
            storage.close()

    # --- datasets (first-class, experiment-independent) ------------------

    @app.get(
        "/api/workspaces/{workspace_id}/datasets",
        response_model=DatasetListPage,
        tags=["datasets"],
    )
    def datasets_index(
        workspace_id: str,
        storage: StorageInterface = Depends(_storage),
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
        storage: StorageInterface = Depends(_storage),
        _user: UserHeader = None,
    ) -> DatasetDetailResponse:
        try:
            detail = dataset_detail(
                storage, workspace_id=workspace_id, dataset_id=dataset_id
            )
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
                db_path=resolved, workspace_id=workspace_id, body=body
            )
        except DatasetWriteError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post(
        "/api/workspaces/{workspace_id}/datasets/upload",
        response_model=DatasetDetailResponse,
        status_code=201,
        tags=["datasets"],
    )
    async def datasets_upload(
        workspace_id: str,
        name: Annotated[str, Form(description="Dataset name.")],
        file: Annotated[UploadFile, File(description="A .jsonl file, one case per line.")],
        dataset_type: Annotated[str, Form()] = "capability",
        description: Annotated[str | None, Form()] = None,
        _user: UserHeader = None,
    ) -> DatasetDetailResponse:
        # Multipart upload of a raw .jsonl — the file-drag path for a FE.
        raw = await file.read()
        try:
            return create_dataset_from_jsonl_bytes(
                db_path=resolved,
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
                db_path=resolved, workspace_id=workspace_id, dataset_id=dataset_id
            )
        except DatasetNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"dataset {dataset_id} not found") from exc
        except DatasetWriteError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get(
        "/api/workspaces/{workspace_id}/metrics/pass-rate",
        response_model=PassRateMetricsResponse,
        tags=["metrics"],
    )
    def metrics_pass_rate(
        workspace_id: str,
        storage: StorageInterface = Depends(_storage),
        start: Annotated[datetime | None, Query(alias="from")] = None,
        end: Annotated[datetime | None, Query(alias="to")] = None,
        experiment_id: str | None = None,
        grader: str | None = None,
        _user: UserHeader = None,
    ) -> PassRateMetricsResponse:
        try:
            return pass_rate_metrics(
                storage,
                workspace_id=workspace_id,
                start=start,
                end=end,
                experiment_id=experiment_id,
                grader=grader,
            )
        finally:
            storage.close()

    @app.get(
        "/api/workspaces/{workspace_id}/metrics/failure-modes",
        response_model=FailureModeMetricsResponse,
        tags=["metrics"],
    )
    def metrics_failure_modes(
        workspace_id: str,
        storage: StorageInterface = Depends(_storage),
        start: Annotated[datetime | None, Query(alias="from")] = None,
        end: Annotated[datetime | None, Query(alias="to")] = None,
        experiment_id: str | None = None,
        grader: str | None = None,
        _user: UserHeader = None,
    ) -> FailureModeMetricsResponse:
        try:
            return failure_mode_metrics(
                storage,
                workspace_id=workspace_id,
                start=start,
                end=end,
                experiment_id=experiment_id,
                grader=grader,
            )
        finally:
            storage.close()

    @app.get(
        "/api/workspaces/{workspace_id}/metrics/tools",
        response_model=ToolMetricsResponse,
        tags=["metrics"],
    )
    def metrics_tools(
        workspace_id: str,
        storage: StorageInterface = Depends(_storage),
        start: Annotated[datetime | None, Query(alias="from")] = None,
        end: Annotated[datetime | None, Query(alias="to")] = None,
        experiment_id: str | None = None,
        tool_name: str | None = None,
        _user: UserHeader = None,
    ) -> ToolMetricsResponse:
        try:
            return tool_metrics(
                storage,
                workspace_id=workspace_id,
                start=start,
                end=end,
                experiment_id=experiment_id,
                tool_name=tool_name,
            )
        finally:
            storage.close()

    @app.get(
        "/api/workspaces/{workspace_id}/metrics/cost",
        response_model=CostMetricsResponse,
        tags=["metrics"],
    )
    def metrics_cost(
        workspace_id: str,
        storage: StorageInterface = Depends(_storage),
        start: Annotated[datetime | None, Query(alias="from")] = None,
        end: Annotated[datetime | None, Query(alias="to")] = None,
        experiment_id: str | None = None,
        model: str | None = None,
        _user: UserHeader = None,
    ) -> CostMetricsResponse:
        try:
            return cost_metrics(
                storage,
                workspace_id=workspace_id,
                start=start,
                end=end,
                experiment_id=experiment_id,
                model=model,
            )
        finally:
            storage.close()

    @app.get(
        "/api/workspaces/{workspace_id}/metrics/tokens",
        response_model=TokenMetricsResponse,
        tags=["metrics"],
    )
    def metrics_tokens(
        workspace_id: str,
        storage: StorageInterface = Depends(_storage),
        start: Annotated[datetime | None, Query(alias="from")] = None,
        end: Annotated[datetime | None, Query(alias="to")] = None,
        experiment_id: str | None = None,
        model: str | None = None,
        _user: UserHeader = None,
    ) -> TokenMetricsResponse:
        try:
            return token_metrics(
                storage,
                workspace_id=workspace_id,
                start=start,
                end=end,
                experiment_id=experiment_id,
                model=model,
            )
        finally:
            storage.close()

    @app.get(
        "/api/workspaces/{workspace_id}/metrics/latency",
        response_model=LatencyMetricsResponse,
        tags=["metrics"],
    )
    def metrics_latency(
        workspace_id: str,
        storage: StorageInterface = Depends(_storage),
        start: Annotated[datetime | None, Query(alias="from")] = None,
        end: Annotated[datetime | None, Query(alias="to")] = None,
        experiment_id: str | None = None,
        _user: UserHeader = None,
    ) -> LatencyMetricsResponse:
        try:
            return latency_metrics(
                storage,
                workspace_id=workspace_id,
                start=start,
                end=end,
                experiment_id=experiment_id,
            )
        finally:
            storage.close()

    @app.get(
        "/api/workspaces/{workspace_id}/experiments",
        response_model=ExperimentListPage,
        tags=["experiments"],
    )
    def experiments_index(
        workspace_id: str,
        storage: StorageInterface = Depends(_storage),
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
            storage_url=resolved,
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
        storage: StorageInterface = Depends(_storage),
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
        storage: StorageInterface = Depends(_storage),
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
        storage: StorageInterface = Depends(_storage),
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
        storage: StorageInterface = Depends(_storage),
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
        storage: StorageInterface = Depends(_storage),
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
        storage: StorageInterface = Depends(_storage),
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
        storage: StorageInterface = Depends(_storage),
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
        storage: StorageInterface = Depends(_storage),
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
        storage: StorageInterface = Depends(_storage),
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

    @app.get(
        "/api/workspaces/{workspace_id}/traces/{trace_id}",
        response_model=TraceResponse,
        tags=["traces"],
        summary="Get a trace by trace id (tr_…) or run id (run_…)",
        description=(
            "Resolves a Trace by **either** its entity id (`tr_…`) **or** its "
            "`run_id` (`run_…`) — both forms are accepted and return the same "
            "trace. This is the canonical id contract across endpoints:\n\n"
            "- `iterations[].trace_run_ids` and `experiments/{id}/results[].run_id` "
            "carry **run ids** (`run_…`).\n"
            "- `experiments/{id}/cases[].latest_trace_id`, `results[].trace_id`, and "
            "`threads[].turns[].trace_id` carry **trace ids** (`tr_…`); each turn also "
            "exposes its `run_id`.\n\n"
            "The response always echoes both `id` (`tr_…`) and `run_id` (`run_…`), so "
            "either field can be used as the navigation key without guessing. Only "
            "traces actually persisted are resolvable; with `persist_traces=\"failed\"` "
            "passing cases have no trace (use `\"all\"` to keep them — see "
            "`SELFEVALS_TRACE_SAMPLING`)."
        ),
    )
    def traces_show(
        workspace_id: str,
        trace_id: str,
        storage: StorageInterface = Depends(_storage),
        _user: UserHeader = None,
    ) -> TraceResponse:
        try:
            trace = load_trace(storage, workspace_id=workspace_id, trace_id=trace_id)
            if trace is None:
                raise HTTPException(status_code=404, detail="trace not found")
            return trace
        finally:
            storage.close()

    @app.get(
        "/api/workspaces/{workspace_id}/threads/{thread_id}",
        response_model=ThreadResponse,
        response_model_exclude_none=True,
        tags=["traces"],
        summary="A conversation thread as ordered per-turn ScenarioResults",
        description=(
            "Every trace sharing `thread_id`, ordered by turn, each projected as a "
            "`ScenarioResult` — the same shape as `/results`, with per-turn "
            "expected/detected/matched and the classified `message`.\n\n"
            "**Migration (breaking):** `turns[]` items are now `ScenarioResult` "
            "(was `ThreadTurn`); use `label` instead of `primary_grade`."
        ),
    )
    def threads_show(
        workspace_id: str,
        thread_id: str,
        storage: StorageInterface = Depends(_storage),
        _user: UserHeader = None,
    ) -> ThreadResponse:
        try:
            thread = load_thread(storage, workspace_id=workspace_id, thread_id=thread_id)
            if thread is None:
                raise HTTPException(status_code=404, detail="thread not found")
            return thread
        finally:
            storage.close()

    @app.get("/api/runs/active", response_model=ActiveRunsResponse, tags=["traces"])
    def runs_active(_user: UserHeader = None) -> ActiveRunsResponse:
        return ActiveRunsResponse(
            runs=[
                ActiveRun(workspace_id=ws, run_id=run)
                for (ws, run) in get_broker().active_runs()
            ]
        )

    @app.get(
        "/api/workspaces/{workspace_id}/traces/{run_id}/stream",
        tags=["traces"],
        response_class=StreamingResponse,
    )
    async def traces_stream(
        workspace_id: str,
        run_id: str,
        _user: UserHeader = None,
    ) -> StreamingResponse:
        return await stream_trace(
            workspace_id=workspace_id,
            run_id=run_id,
            broker=get_broker(),
            storage_factory=_storage_factory,
        )

    @app.get(
        "/api/workspaces/{workspace_id}/anchor-set",
        response_model=list[AnchorPoint],
        tags=["anchor-set"],
    )
    def anchor_set(
        workspace_id: str,
        storage: StorageInterface = Depends(_storage),
        _user: UserHeader = None,
    ) -> list[AnchorPoint]:
        try:
            return anchor_set_history(storage, workspace_id=workspace_id)
        finally:
            storage.close()

    @app.get(
        "/api/workspaces/{workspace_id}/payloads",
        tags=["traces"],
        responses={
            200: {
                "description": "Resolved payload bytes (JSON when parseable, raw otherwise).",
            },
            400: {"description": "Invalid pointer or workspace mismatch."},
            404: {"description": "Pointer not found in the object store."},
        },
    )
    def resolve_payload(
        workspace_id: str,
        pointer: Annotated[
            str,
            Query(
                description=(
                    "Object-store pointer of the form `oss://<workspace_id>/sha256:<hex>`. "
                    "Used to lazy-load LLM prompts, tool call args/results, and retrieval "
                    "payloads in the trace viewer (the spans only carry the pointers + "
                    "hashes; this endpoint resolves them on demand)."
                ),
            ),
        ],
        _user: UserHeader = None,
    ) -> Response:
        # Pointers carry their workspace inside; we still require the path
        # workspace to match so a leaked pointer from one workspace can't be
        # read via another workspace's URL. The same content_hash can appear
        # in multiple workspaces (content-addressed), so cross-workspace
        # reads via path-mismatch must 400, not 404.
        try:
            ptr_workspace, _ptr_hash = parse_pointer(pointer)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if ptr_workspace != workspace_id:
            raise HTTPException(
                status_code=400,
                detail="pointer workspace does not match path workspace",
            )
        try:
            data = object_store.get(pointer)
        except ObjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail="payload not found") from exc
        except PointerHashMismatchError as exc:
            # Stored content's hash no longer matches the pointer — surface
            # loudly so the FE can show a corruption warning instead of
            # silently rendering wrong bytes.
            raise HTTPException(
                status_code=500,
                detail=f"stored payload hash mismatch: {exc}",
            ) from exc
        # Most LLM/tool payloads are JSON; serve as JSON when parseable so
        # the FE can render them structurally. Fall back to text/plain for
        # everything else (e.g. raw markdown). We deliberately don't expose
        # arbitrary content-types — payloads in this store are always text.
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            return Response(content=data, media_type="application/octet-stream")
        # Cheap JSON sniff: don't parse, just check the first non-whitespace
        # character. The FE will JSON.parse on its side if appropriate.
        stripped = data.lstrip()
        is_jsonish = stripped.startswith((b"{", b"[", b'"'))
        media = "application/json" if is_jsonish else "text/plain; charset=utf-8"
        return Response(content=data, media_type=media)

    return app
