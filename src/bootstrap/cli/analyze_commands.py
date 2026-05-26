"""CLI commands for error analysis: `analyze pull/push` and `failuremode *`.

These implement the handshake (design §4) and the human promotion gate (§6).
`analyze pull` emits an AnalysisBundle as JSON on stdout; `analyze push` reads
an AnalysisResult as JSON on stdin. The `failuremode` family manages the
taxonomy: list, promote (candidate→official), retire, merge, edit.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bootstrap._errors import BootstrapUserError
from bootstrap.analysis import build_bundle, ingest_result
from bootstrap.analysis.ingest import AnalysisIngestError
from bootstrap.analysis.schemas import AnalysisResult
from bootstrap.cli.commands import _require_entity, _storage
from bootstrap.schemas.enums import FailureModeStatus
from bootstrap.schemas.failure_mode import FailureMode
from bootstrap.storage.filesystem import FilesystemObjectStore
from bootstrap.storage.interface import ListFilter


def _object_store(args: argparse.Namespace) -> FilesystemObjectStore:
    """Object store rooted next to the db, for payload-routed quotes."""
    return FilesystemObjectStore(Path(args.db).parent / "objects")


# --- analyze ----------------------------------------------------------------


def cmd_analyze_pull(args: argparse.Namespace) -> int:
    storage = _storage(args)
    try:
        bundle = build_bundle(
            storage,
            workspace_id=args.workspace_id,
            experiment_id=args.experiment_id,
            iteration=args.iteration,
            only_failed=not args.all,
        )
    finally:
        storage.close()
    print(json.dumps(bundle.model_dump(mode="json"), indent=2))
    return 0


def cmd_analyze_push(args: argparse.Namespace) -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        raise BootstrapUserError("analyze push expects an AnalysisResult JSON on stdin")
    try:
        result = AnalysisResult.model_validate_json(raw)
    except ValueError as exc:
        raise BootstrapUserError(f"invalid AnalysisResult JSON: {exc}") from exc

    storage = _storage(args)
    try:
        summary = ingest_result(
            storage,
            workspace_id=args.workspace_id,
            experiment_id=args.experiment_id,
            result=result,
            proposed_by=args.by,
            object_store=_object_store(args),
        )
    except AnalysisIngestError as exc:
        raise BootstrapUserError(str(exc)) from exc
    finally:
        storage.close()

    print(f"assignments applied : {summary.assignments_applied}")
    print(f"candidates created  : {len(summary.created_candidates)}")
    print(f"candidates re-seen  : {len(summary.updated_candidates)}")
    print(f"hypotheses recorded : {summary.hypotheses_recorded}")
    if summary.created_candidates:
        print("\nnew candidates (promote with `bootstrap failuremode promote <id>`):")
        for fm_id in summary.created_candidates:
            print(f"  {fm_id}")
    return 0


# --- failuremode ------------------------------------------------------------


def cmd_failuremode_list(args: argparse.Namespace) -> int:
    storage = _storage(args)
    try:
        with storage.open(args.workspace_id) as scope:
            modes = [
                m
                for m in scope.list_entities(FailureMode, ListFilter())
                if isinstance(m, FailureMode)
            ]
    finally:
        storage.close()
    if args.status:
        modes = [m for m in modes if str(m.status) == args.status]
    if not modes:
        print("(no failure modes)")
        return 0
    for m in sorted(modes, key=lambda x: (str(x.status), x.slug)):
        marker = "*" if m.status == FailureModeStatus.OFFICIAL else " "
        print(f"{marker} {m.id}  [{m.status}]  {m.slug}  ({len(m.examples)} ex)")
    return 0


def _load_mode(args: argparse.Namespace, fm_id: str) -> FailureMode:
    storage = _storage(args)
    try:
        with storage.open(args.workspace_id) as scope:
            fm = _require_entity(scope, FailureMode, fm_id)
    finally:
        storage.close()
    assert isinstance(fm, FailureMode)
    return fm


def _save_mode(args: argparse.Namespace, fm: FailureMode) -> None:
    storage = _storage(args)
    try:
        with storage.open(args.workspace_id) as scope:
            scope.put_entity(fm)
    finally:
        storage.close()


def cmd_failuremode_promote(args: argparse.Namespace) -> int:
    fm = _load_mode(args, args.failure_mode_id)
    if fm.status == FailureModeStatus.OFFICIAL:
        print(f"{fm.id} is already official")
        return 0
    fm.status = FailureModeStatus.OFFICIAL
    _save_mode(args, fm)
    print(f"promoted {fm.id} ({fm.slug}) → official")
    return 0


def cmd_failuremode_retire(args: argparse.Namespace) -> int:
    fm = _load_mode(args, args.failure_mode_id)
    fm.status = FailureModeStatus.RETIRED
    _save_mode(args, fm)
    print(f"retired {fm.id} ({fm.slug})")
    return 0


def cmd_failuremode_merge(args: argparse.Namespace) -> int:
    src = _load_mode(args, args.failure_mode_id)
    dst = _load_mode(args, args.into)
    if src.id == dst.id:
        raise BootstrapUserError("cannot merge a mode into itself")
    # Move examples to the destination, retire the source, set the back-pointer.
    dst.examples = [*dst.examples, *src.examples]
    src.superseded_by = dst.id
    src.status = FailureModeStatus.RETIRED
    _save_mode(args, dst)
    _save_mode(args, src)
    print(f"merged {src.id} ({src.slug}) → {dst.id} ({dst.slug}); source retired")
    return 0


def cmd_failuremode_edit(args: argparse.Namespace) -> int:
    fm = _load_mode(args, args.failure_mode_id)
    if args.title is None and args.definition is None:
        raise BootstrapUserError("nothing to edit: pass --title and/or --definition")
    if args.title is not None:
        fm.title = args.title
    if args.definition is not None:
        fm.definition = args.definition
    _save_mode(args, fm)
    print(f"edited {fm.id} ({fm.slug})")
    return 0
