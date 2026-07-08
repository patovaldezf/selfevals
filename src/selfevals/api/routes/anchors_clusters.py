"""Anchor-set history and failure-mode clusters (§J.6)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import Depends, FastAPI, Query

from selfevals.api.auth import UserHeader
from selfevals.api.deps import AppDeps
from selfevals.api.metrics import failure_clusters
from selfevals.api.queries import AnchorPoint, anchor_set_history
from selfevals.api.schemas import FailureClustersResponse
from selfevals.storage.interface import StorageInterface


def register(app: FastAPI, deps: AppDeps) -> None:
    @app.get(
        "/api/workspaces/{workspace_id}/anchor-set",
        response_model=list[AnchorPoint],
        tags=["anchor-set"],
    )
    def anchor_set(
        workspace_id: str,
        storage: StorageInterface = Depends(deps.storage),
        _user: UserHeader = None,
    ) -> list[AnchorPoint]:
        try:
            return anchor_set_history(storage, workspace_id=workspace_id)
        finally:
            storage.close()

    @app.get(
        "/api/workspaces/{workspace_id}/clusters",
        response_model=FailureClustersResponse,
        tags=["clusters"],
        summary="Failing traces grouped by failure mode (§J.6)",
        description=(
            "Groups failing traces by their stable failure-mode slug, ranked by "
            "frequency, with capped example `run_id`s for drill-down and "
            "title/status enriched from the workspace taxonomy. v1 clusters by the "
            "existing taxonomy (cluster ≡ mode); semantic clustering is future work."
        ),
    )
    def clusters(
        workspace_id: str,
        storage: StorageInterface = Depends(deps.storage),
        start: Annotated[datetime | None, Query(alias="from")] = None,
        end: Annotated[datetime | None, Query(alias="to")] = None,
        experiment_id: str | None = None,
        grader: str | None = None,
        limit: Annotated[int | None, Query(ge=1, le=200)] = None,
        _user: UserHeader = None,
    ) -> FailureClustersResponse:
        try:
            return failure_clusters(
                storage,
                workspace_id=workspace_id,
                start=start,
                end=end,
                experiment_id=experiment_id,
                grader=grader,
                limit=limit,
            )
        finally:
            storage.close()
