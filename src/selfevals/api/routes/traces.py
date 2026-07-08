"""Trace/thread retrieval, live SSE streaming, case-draft promotion, and payload resolution."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import Response, StreamingResponse

from selfevals.api.auth import UserHeader, readable_workspace_ids
from selfevals.api.broker import get_broker
from selfevals.api.dataset_writer import TracePromotionError, draft_regression_case_from_trace
from selfevals.api.deps import AppDeps
from selfevals.api.queries import load_thread, load_trace
from selfevals.api.schemas import (
    ActiveRun,
    ActiveRunsResponse,
    PromoteCaseDraftRequest,
    PromoteCaseDraftResponse,
    ThreadResponse,
    TraceResponse,
)
from selfevals.api.sse import stream_trace
from selfevals.storage.errors import ObjectNotFoundError, PointerHashMismatchError
from selfevals.storage.filesystem import parse_pointer
from selfevals.storage.interface import StorageInterface


def register(app: FastAPI, deps: AppDeps) -> None:
    _register_trace_detail(app, deps)
    _register_streaming_and_payloads(app, deps)


def _register_trace_detail(app: FastAPI, deps: AppDeps) -> None:
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
            'traces actually persisted are resolvable; with `persist_traces="failed"` '
            'passing cases have no trace (use `"all"` to keep them — see '
            "`SELFEVALS_TRACE_SAMPLING`)."
        ),
    )
    def traces_show(
        workspace_id: str,
        trace_id: str,
        storage: StorageInterface = Depends(deps.storage),
        _user: UserHeader = None,
    ) -> TraceResponse:
        try:
            trace = load_trace(storage, workspace_id=workspace_id, trace_id=trace_id)
            if trace is None:
                raise HTTPException(status_code=404, detail="trace not found")
            return trace
        finally:
            storage.close()

    @app.post(
        "/api/workspaces/{workspace_id}/traces/{trace_id}/case-draft",
        response_model=PromoteCaseDraftResponse,
        tags=["traces"],
    )
    def traces_case_draft(
        workspace_id: str,
        trace_id: str,
        body: PromoteCaseDraftRequest | None = None,
        storage: StorageInterface = Depends(deps.storage),
        _user: UserHeader = None,
    ) -> PromoteCaseDraftResponse:
        try:
            return draft_regression_case_from_trace(
                storage,
                workspace_id=workspace_id,
                trace_id=trace_id,
                body=body,
            )
        except TracePromotionError as exc:
            message = str(exc)
            status = 404 if "not found" in message else 422
            raise HTTPException(status_code=status, detail=message) from exc
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
        storage: StorageInterface = Depends(deps.storage),
        _user: UserHeader = None,
    ) -> ThreadResponse:
        try:
            thread = load_thread(storage, workspace_id=workspace_id, thread_id=thread_id)
            if thread is None:
                raise HTTPException(status_code=404, detail="thread not found")
            return thread
        finally:
            storage.close()


def _register_streaming_and_payloads(app: FastAPI, deps: AppDeps) -> None:
    @app.get("/api/runs/active", response_model=ActiveRunsResponse, tags=["traces"])
    def runs_active(
        storage: StorageInterface = Depends(deps.storage),
        _user: UserHeader = None,
    ) -> ActiveRunsResponse:
        try:
            active = get_broker().active_runs()
            allowed = readable_workspace_ids(
                storage, candidate_ids=[ws for (ws, _run) in active], user=_user
            )
            if allowed is not None:
                active = [(ws, run) for (ws, run) in active if ws in allowed]
            return ActiveRunsResponse(
                runs=[ActiveRun(workspace_id=ws, run_id=run) for (ws, run) in active]
            )
        finally:
            storage.close()

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
            storage_factory=deps.storage_factory,
        )

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
            data = deps.object_store.get(pointer)
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
