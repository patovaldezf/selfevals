"""Workspace listing and detail queries."""

from __future__ import annotations

from collections.abc import Sequence

from selfevals.api.schemas import WorkspaceResponse, WorkspaceSummary
from selfevals.schemas.experiment import Experiment
from selfevals.schemas.iteration import IterationRecord
from selfevals.schemas.workspace import Workspace
from selfevals.storage.errors import EntityNotFoundError
from selfevals.storage.interface import ListFilter, StorageInterface


def list_workspaces(storage: StorageInterface) -> list[WorkspaceSummary]:
    """Cross-workspace listing. The storage backend exposes a dedicated
    summary method because the typed scope interface is intentionally
    scoped — no way to list without a workspace_id."""
    return storage.list_workspace_summaries()


def workspace_detail(storage: StorageInterface, *, workspace_id: str) -> WorkspaceResponse | None:
    try:
        with storage.open(workspace_id) as scope:
            ws = scope.get_entity(Workspace, workspace_id)
            assert isinstance(ws, Workspace)
            experiments: Sequence[Experiment] = [
                e
                for e in scope.list_entities(Experiment, ListFilter())
                if isinstance(e, Experiment)
            ]
            recent_iterations = [
                it
                for it in scope.list_entities(
                    IterationRecord,
                    ListFilter(order_by="updated_at", limit=20),
                )
                if isinstance(it, IterationRecord)
            ]
    except EntityNotFoundError:
        return None
    keep_count = sum(
        1
        for it in recent_iterations
        if it.decision is not None and str(it.decision.outcome) == "keep_candidate"
    )
    recent_health: float | None = None
    if recent_iterations:
        recent_health = round(keep_count / len(recent_iterations), 3)
    return WorkspaceResponse(
        id=ws.id,
        slug=ws.slug,
        name=ws.name,
        description=ws.description,
        owner_id=ws.owner_id,
        created_at=ws.created_at,
        experiment_count=len(experiments),
        recent_health=recent_health,
    )
