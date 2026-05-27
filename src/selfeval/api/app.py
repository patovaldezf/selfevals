"""FastAPI app — read-mostly HTTP bridge over the SQLite store.

Mounted on `/` (no version prefix; this is a single internal service).
Endpoints map 1:1 to the pages of the web UI; payload shapes match
the existing Pydantic models so the web side can validate against the
same canonical JSON.

Auth: stubbed via a single `X-SelfEval-User` header (default
`"local"`). Real auth lands later; everything else is forward-compat.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from selfeval.api.broker import get_broker
from selfeval.api.queries import (
    AnchorPoint,
    anchor_set_history,
    experiment_decisions,
    experiment_detail,
    experiment_iterations,
    iteration_detail,
    list_experiments,
    list_workspaces,
    load_thread,
    load_trace,
    workspace_detail,
)
from selfeval.api.schemas import (
    CreateWorkspaceRequest,
    ExperimentDetailResponse,
    HealthResponse,
    IterationListResponse,
    ThreadResponse,
    TraceResponse,
    WorkspaceListResponse,
    WorkspaceResponse,
)
from selfeval.api.sse import stream_trace
from selfeval.storage.sqlite import SQLiteStorage

DEFAULT_DB_PATH = "./selfeval.sqlite"
_USER_HEADER = "X-SelfEval-User"

UserHeader = Annotated[
    str | None,
    Header(alias=_USER_HEADER, description="Stubbed user id (auth is post-MVP)."),
]


def _resolve_db_path(db_path: str | None) -> str:
    return db_path or os.environ.get("SELFEVAL_DB", DEFAULT_DB_PATH)


def build_app(*, db_path: str | None = None) -> FastAPI:
    """Construct the FastAPI app, parameterized on the SQLite db path."""
    resolved = _resolve_db_path(db_path)
    Path(resolved).parent.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # Capture the running event loop so the OTLP receiver thread
        # (which runs sync) can schedule span publishes onto it.
        get_broker().bind_loop(asyncio.get_running_loop())
        yield

    app = FastAPI(
        title="selfeval",
        description="HTTP bridge for the selfeval evals framework.",
        version="0.0.1",
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    def _storage() -> SQLiteStorage:
        return SQLiteStorage(resolved)

    def _storage_factory() -> SQLiteStorage:
        return SQLiteStorage(resolved)

    @app.get("/api/health", response_model=HealthResponse, tags=["meta"])
    def health() -> HealthResponse:
        return HealthResponse(status="ok", db_path=resolved)

    @app.get(
        "/api/workspaces",
        response_model=WorkspaceListResponse,
        tags=["workspaces"],
    )
    def workspaces_index(
        storage: SQLiteStorage = Depends(_storage),
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
        storage: SQLiteStorage = Depends(_storage),
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
        storage: SQLiteStorage = Depends(_storage),
        user: UserHeader = None,
    ) -> WorkspaceResponse:
        from selfeval.storage.seed import seed_workspace

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

    @app.get(
        "/api/workspaces/{workspace_id}/experiments",
        response_model=list[dict[str, Any]],
        tags=["experiments"],
    )
    def experiments_index(
        workspace_id: str,
        storage: SQLiteStorage = Depends(_storage),
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        _user: UserHeader = None,
    ) -> list[dict[str, Any]]:
        try:
            return list_experiments(storage, workspace_id=workspace_id, limit=limit)
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
        storage: SQLiteStorage = Depends(_storage),
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
        storage: SQLiteStorage = Depends(_storage),
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
        "/api/workspaces/{workspace_id}/experiments/{experiment_id}/decisions",
        tags=["experiments"],
    )
    def experiments_decisions(
        workspace_id: str,
        experiment_id: str,
        storage: SQLiteStorage = Depends(_storage),
        _user: UserHeader = None,
    ) -> list[dict[str, Any]]:
        try:
            return experiment_decisions(
                storage,
                workspace_id=workspace_id,
                experiment_id=experiment_id,
            )
        finally:
            storage.close()

    @app.get(
        "/api/workspaces/{workspace_id}/iterations/{iteration_id}",
        tags=["experiments"],
    )
    def iterations_show(
        workspace_id: str,
        iteration_id: str,
        storage: SQLiteStorage = Depends(_storage),
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
        "/api/workspaces/{workspace_id}/traces/{trace_id}",
        response_model=TraceResponse,
        tags=["traces"],
    )
    def traces_show(
        workspace_id: str,
        trace_id: str,
        storage: SQLiteStorage = Depends(_storage),
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
        tags=["traces"],
    )
    def threads_show(
        workspace_id: str,
        thread_id: str,
        storage: SQLiteStorage = Depends(_storage),
        _user: UserHeader = None,
    ) -> ThreadResponse:
        try:
            thread = load_thread(storage, workspace_id=workspace_id, thread_id=thread_id)
            if thread is None:
                raise HTTPException(status_code=404, detail="thread not found")
            return thread
        finally:
            storage.close()

    @app.get("/api/runs/active", tags=["traces"])
    def runs_active(_user: UserHeader = None) -> list[dict[str, str]]:
        return [{"workspace_id": ws, "run_id": run} for (ws, run) in get_broker().active_runs()]

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
        storage: SQLiteStorage = Depends(_storage),
        _user: UserHeader = None,
    ) -> list[AnchorPoint]:
        try:
            return anchor_set_history(storage, workspace_id=workspace_id)
        finally:
            storage.close()

    return app
