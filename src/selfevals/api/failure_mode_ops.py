"""HTTP-facing operations over the failure-mode taxonomy.

Mirrors the logic in `cli/analyze_commands.py` (list / promote / retire / merge
/ edit) but operates directly on a `StorageInterface` + `WorkspaceScope` so the
API can call it without an argparse namespace. The promotion gate (candidate →
official → retired) and the merge invariant (history preserved via
`superseded_by`, never deleted) are identical to the CLI — this is the same
behavior exposed over a second surface, not a fork.
"""

from __future__ import annotations

from selfevals.api.schemas import FailureModeResponse
from selfevals.schemas.enums import FailureModeStatus
from selfevals.schemas.failure_mode import FailureMode
from selfevals.storage.interface import ListFilter, StorageInterface


class FailureModeNotFoundError(Exception):
    """The requested mode does not exist in the workspace."""


class FailureModeOpError(Exception):
    """A taxonomy operation was rejected (e.g. merge into self)."""


def _view(fm: FailureMode) -> FailureModeResponse:
    return FailureModeResponse(
        id=fm.id,
        slug=fm.slug,
        title=fm.title,
        definition=fm.definition,
        status=str(fm.status),
        parent_mode_id=fm.parent_mode_id,
        proposed_by=fm.proposed_by,
        example_count=len(fm.examples),
        first_seen_iteration=fm.first_seen_iteration,
        superseded_by=fm.superseded_by,
        created_at=fm.created_at,
        updated_at=fm.updated_at,
    )


def list_failure_modes(
    storage: StorageInterface, *, workspace_id: str, status: str | None = None
) -> list[FailureModeResponse]:
    with storage.open(workspace_id) as scope:
        modes = [
            m for m in scope.list_entities(FailureMode, ListFilter()) if isinstance(m, FailureMode)
        ]
    if status:
        modes = [m for m in modes if str(m.status) == status]
    modes.sort(key=lambda m: (str(m.status), m.slug))
    return [_view(m) for m in modes]


def _load(storage: StorageInterface, *, workspace_id: str, fm_id: str) -> FailureMode:
    with storage.open(workspace_id) as scope:
        try:
            fm = scope.get_entity(FailureMode, fm_id)
        except Exception as exc:
            raise FailureModeNotFoundError(f"failure mode {fm_id} not found") from exc
    assert isinstance(fm, FailureMode)
    return fm


def _save(storage: StorageInterface, *, workspace_id: str, fm: FailureMode) -> None:
    with storage.open(workspace_id) as scope:
        scope.put_entity(fm)


def promote_failure_mode(
    storage: StorageInterface, *, workspace_id: str, fm_id: str
) -> FailureModeResponse:
    fm = _load(storage, workspace_id=workspace_id, fm_id=fm_id)
    if fm.status != FailureModeStatus.OFFICIAL:
        fm.status = FailureModeStatus.OFFICIAL
        _save(storage, workspace_id=workspace_id, fm=fm)
    return _view(fm)


def retire_failure_mode(
    storage: StorageInterface, *, workspace_id: str, fm_id: str
) -> FailureModeResponse:
    fm = _load(storage, workspace_id=workspace_id, fm_id=fm_id)
    fm.status = FailureModeStatus.RETIRED
    _save(storage, workspace_id=workspace_id, fm=fm)
    return _view(fm)


def merge_failure_modes(
    storage: StorageInterface, *, workspace_id: str, fm_id: str, into_id: str
) -> FailureModeResponse:
    if fm_id == into_id:
        raise FailureModeOpError("cannot merge a mode into itself")
    src = _load(storage, workspace_id=workspace_id, fm_id=fm_id)
    dst = _load(storage, workspace_id=workspace_id, fm_id=into_id)
    # Move examples to the destination, retire the source, set the back-pointer.
    dst.examples = [*dst.examples, *src.examples]
    src.superseded_by = dst.id
    src.status = FailureModeStatus.RETIRED
    _save(storage, workspace_id=workspace_id, fm=dst)
    _save(storage, workspace_id=workspace_id, fm=src)
    return _view(dst)


def edit_failure_mode(
    storage: StorageInterface,
    *,
    workspace_id: str,
    fm_id: str,
    title: str | None = None,
    definition: str | None = None,
) -> FailureModeResponse:
    fm = _load(storage, workspace_id=workspace_id, fm_id=fm_id)
    if title is not None:
        fm.title = title
    if definition is not None:
        fm.definition = definition
    _save(storage, workspace_id=workspace_id, fm=fm)
    return _view(fm)
