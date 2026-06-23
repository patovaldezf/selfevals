"""`selfevals migrate-sqlite` — one-shot import of a legacy SQLite database.

Reads the old generic ``entities`` table (the pre-Postgres schema, where every
entity lived as a JSON ``payload`` keyed by ``entity_type``) and writes each row
through the current Postgres storage, so the normalized tables, constraints, and
projections are all populated the same way a fresh write would.

This is the ONLY place ``sqlite3`` survives in the codebase: a read-only reader
for the file being migrated. It never writes SQLite.
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import pkgutil
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

import selfevals
from selfevals.schemas._base import BaseEntity
from selfevals.storage.factory import open_storage

if TYPE_CHECKING:
    from selfevals.storage.interface import StorageInterface

# Parent-first order so non-deferred FKs (and readability) are satisfied as we
# stream rows in. Entity types not listed fall to the end in name order.
_WRITE_ORDER = [
    "Workspace",
    "Member",
    "FeatureRegistry",
    "RiskRegistry",
    "Tool",
    "Agent",
    "AgentFleet",
    "GraderCard",
    "Experiment",
    "EvalCase",
    "Dataset",
    "DatasetBaseline",
    "IterationRecord",
    "DecisionRecord",
    "Trace",
    "FailureMode",
    "Annotation",
    "HypothesisRecord",
    "AnalysisStagingRecord",
    "RunJob",
]


def _entity_registry() -> dict[str, type[BaseEntity]]:
    """Map entity class name -> class, scanning the whole package."""
    registry: dict[str, type[BaseEntity]] = {}
    for mod in pkgutil.walk_packages(selfevals.__path__, "selfevals."):
        try:
            module = importlib.import_module(mod.name)
        except Exception:  # pragma: no cover - optional extras may not import
            continue
        for _name, obj in vars(module).items():
            if (
                inspect.isclass(obj)
                and issubclass(obj, BaseEntity)
                and obj is not BaseEntity
            ):
                registry[obj.__name__] = obj
    return registry


def _read_legacy_entities(
    sqlite_path: str,
) -> list[tuple[str, str, str]]:
    """Return (entity_type, workspace_id, payload_json) rows from the old DB."""
    uri = f"file:{Path(sqlite_path).resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        rows = conn.execute(
            "SELECT entity_type, workspace_id, payload FROM entities ORDER BY entity_type"
        ).fetchall()
    finally:
        conn.close()
    return [(str(r[0]), str(r[1]), str(r[2])) for r in rows]


def _order_key(entity_type: str) -> tuple[int, str]:
    try:
        return (_WRITE_ORDER.index(entity_type), entity_type)
    except ValueError:
        return (len(_WRITE_ORDER), entity_type)


def cmd_migrate_sqlite(args: argparse.Namespace) -> int:
    source: str = args.source
    target: str = args.to
    dry_run: bool = args.dry_run

    if not Path(source).exists():
        print(f"error: source SQLite file not found: {source}")
        return 1

    registry = _entity_registry()
    rows = _read_legacy_entities(source)
    print(f"read {len(rows)} entities from {source}")

    # Validate + bucket by workspace, in write order.
    by_ws: dict[str, list[BaseEntity]] = {}
    skipped: list[str] = []
    for entity_type, workspace_id, payload in rows:
        cls = registry.get(entity_type)
        if cls is None:
            skipped.append(entity_type)
            continue
        entity = cls.model_validate(json.loads(payload))
        by_ws.setdefault(workspace_id, []).append(entity)

    for ws_id in by_ws:
        by_ws[ws_id].sort(key=lambda e: _order_key(type(e).__name__))

    counts: dict[str, int] = {}
    for entities in by_ws.values():
        for e in entities:
            counts[type(e).__name__] = counts.get(type(e).__name__, 0) + 1

    print("entities by type:")
    for name in sorted(counts):
        print(f"  {name}: {counts[name]}")
    if skipped:
        print(f"skipped unknown entity types: {sorted(set(skipped))}")

    if dry_run:
        print("dry-run: nothing written")
        return 0

    storage: StorageInterface = open_storage(target)
    written = 0
    try:
        for ws_id, entities in by_ws.items():
            with storage.transaction(), storage.open(ws_id) as scope:  # type: ignore[attr-defined]
                for e in entities:
                    scope.put_entity(e)
                    written += 1
    finally:
        storage.close()

    print(f"migrated {written} entities into {target}")
    return 0
