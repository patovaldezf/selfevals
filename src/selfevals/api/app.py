"""FastAPI app — read-mostly HTTP bridge over configured storage.

Mounted on `/` (no version prefix; this is a single internal service).
Endpoints map 1:1 to the pages of the web UI; payload shapes match
the existing Pydantic models so the web side can validate against the
same canonical JSON.

Auth is centralized in `api.auth`. Local development accepts
`X-SelfEvals-User` with a `"local"` fallback; shared deployments should set
`SELFEVALS_AUTH_MODE=token` (signed session tokens, see `api.tokens`) before
exposing the API to untrusted callers.

Routes are split by resource under `api.routes.*`; this module only builds
the app, wires shared middleware/dependencies, and registers each route
module. See `api.deps.AppDeps` for the storage/object-store wiring handed to
every route module.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from selfevals.api.auth import USER_HEADER, auth_mode, authorize_workspace
from selfevals.api.broker import get_broker
from selfevals.api.deps import AppDeps
from selfevals.api.routes import (
    analysis,
    anchors_clusters,
    arena,
    datasets,
    experiments,
    failure_modes,
    meta,
    metrics,
    pairwise,
    traces,
    workspaces,
)
from selfevals.api.routes import (
    auth as auth_routes,
)
from selfevals.storage.factory import object_store_base_for_storage_url, resolve_storage_url
from selfevals.storage.filesystem import FilesystemObjectStore

logger = logging.getLogger(__name__)

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


def build_app(*, db_path: str | None = None) -> FastAPI:
    """Construct the FastAPI app, parameterized on the storage URL."""
    resolved = resolve_storage_url(db_path)
    # Filesystem object store (SELFEVALS_OBJECTS_DIR, default ./objects) for
    # large trace payloads. The store is process-local and cheap to construct,
    # so we build one app-wide instance rather than per-request.
    object_store = FilesystemObjectStore(object_store_base_for_storage_url(resolved))
    deps = AppDeps(storage_url=resolved, object_store=object_store)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # Capture the running event loop so the OTLP receiver thread
        # (which runs sync) can schedule span publishes onto it.
        get_broker().bind_loop(asyncio.get_running_loop())
        # `local` is the default and skips authorization entirely, so the only
        # thing standing between an exposed port and full access is that nobody
        # found it. Say so once at startup rather than leaving it to be
        # discovered by reading auth.py.
        if auth_mode() == "local":
            logger.warning(
                "auth mode is 'local': every request is trusted and workspace "
                "authorization is skipped. Set SELFEVALS_AUTH_MODE=token before "
                "binding to a non-loopback address."
            )
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
        allow_methods=["GET", "POST", "PATCH", "PUT", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def _authorize_workspace_routes(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Authorize non-local callers for workspace-scoped API routes."""
        parts = [part for part in request.url.path.split("/") if part]
        if len(parts) >= 3 and parts[0] == "api" and parts[1] == "workspaces":
            workspace_id = parts[2]
            write = request.method.upper() not in {"GET", "HEAD", "OPTIONS"}
            store = deps.storage_factory()
            try:
                try:
                    authorize_workspace(
                        store,
                        workspace_id=workspace_id,
                        user=request.headers.get(USER_HEADER),
                        write=write,
                    )
                except HTTPException as exc:
                    return JSONResponse(
                        status_code=exc.status_code,
                        content={"detail": exc.detail},
                    )
            finally:
                store.close()
        return await call_next(request)

    for route_module in (
        meta,
        auth_routes,
        workspaces,
        datasets,
        pairwise,
        failure_modes,
        analysis,
        metrics,
        experiments,
        traces,
        anchors_clusters,
        arena,
    ):
        route_module.register(app, deps)

    return app
