"""`selfevals migrate-sqlite` imports a legacy generic-schema SQLite DB into PG."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from selfevals.cli.migrate_commands import cmd_migrate_sqlite
from selfevals.schemas.experiment import Experiment
from selfevals.schemas.workspace import Workspace

if TYPE_CHECKING:
    from selfevals.storage.interface import StorageInterface


def _legacy_sqlite(path: Path, entities: list[tuple[str, str, str, dict[str, object]]]) -> None:
    """Write a minimal old-schema `entities` table (entity_type/id/workspace_id/payload)."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE entities (
            entity_type  TEXT NOT NULL,
            id           TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            version      INTEGER NOT NULL,
            created_at   TEXT NOT NULL,
            updated_at   TEXT NOT NULL,
            deleted_at   TEXT,
            payload      TEXT NOT NULL,
            PRIMARY KEY (entity_type, id)
        )
        """
    )
    for entity_type, entity_id, workspace_id, payload in entities:
        conn.execute(
            "INSERT INTO entities (entity_type, id, workspace_id, version, created_at, "
            "updated_at, deleted_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entity_type,
                entity_id,
                workspace_id,
                payload["version"],
                payload["created_at"],
                payload["updated_at"],
                None,
                json.dumps(payload),
            ),
        )
    conn.commit()
    conn.close()


def test_migrate_sqlite_imports_entities(tmp_path: Path, db_url: str, storage: StorageInterface) -> None:
    ws_id = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"
    ws = Workspace(id=ws_id, workspace_id=ws_id, slug="legacy", name="Legacy")
    exp = Experiment.model_validate(
        {
            **_minimal_experiment_payload(ws_id),
        }
    )
    legacy = tmp_path / "old.sqlite"
    _legacy_sqlite(
        legacy,
        [
            ("Workspace", ws.id, ws_id, ws.model_dump(mode="json")),
            ("Experiment", exp.id, ws_id, exp.model_dump(mode="json")),
        ],
    )

    args = argparse.Namespace(source=str(legacy), to=db_url, dry_run=False)
    rc = cmd_migrate_sqlite(args)
    assert rc == 0

    # The entities are now readable through the Postgres backend.
    with storage.open(ws_id) as scope:
        got_ws = scope.get_entity(Workspace, ws.id)
        assert isinstance(got_ws, Workspace)
        assert got_ws.slug == "legacy"
        got_exp = scope.get_entity(Experiment, exp.id)
        assert isinstance(got_exp, Experiment)


def test_migrate_sqlite_dry_run_writes_nothing(
    tmp_path: Path, db_url: str, storage: StorageInterface
) -> None:
    ws_id = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"
    ws = Workspace(id=ws_id, workspace_id=ws_id, slug="legacy", name="Legacy")
    legacy = tmp_path / "old.sqlite"
    _legacy_sqlite(legacy, [("Workspace", ws.id, ws_id, ws.model_dump(mode="json"))])

    args = argparse.Namespace(source=str(legacy), to=db_url, dry_run=True)
    assert cmd_migrate_sqlite(args) == 0

    with storage.open(ws_id) as scope:
        assert not scope.exists(Workspace, ws.id)


def test_migrate_sqlite_missing_file(db_url: str) -> None:
    args = argparse.Namespace(source="/nonexistent/x.sqlite", to=db_url, dry_run=False)
    assert cmd_migrate_sqlite(args) == 1


def _minimal_experiment_payload(workspace_id: str) -> dict[str, object]:
    from selfevals.schemas._base import EntityRef
    from selfevals.schemas.enums import DatasetType, Mode, ProposerStrategy, SandboxMode
    from selfevals.schemas.experiment import (
        DatasetUsage,
        ExperimentTaxonomy,
        FrozenSnapshot,
        MetricTarget,
        ProposerSpec,
        RunSpec,
        TargetSpec,
    )

    exp = Experiment(
        id=Experiment.make_id(),
        workspace_id=workspace_id,
        name="e",
        goal="g",
        mode=Mode.HANDOFF,
        taxonomy=ExperimentTaxonomy(
            target_features=["commerce.x"], dataset_types=[DatasetType.CAPABILITY]
        ),
        datasets=DatasetUsage(optimization=EntityRef(id="ds_x")),
        target=TargetSpec(primary=MetricTarget(name="pass@1", operator=">=", value=0.5)),
        frozen=FrozenSnapshot(
            fleet=EntityRef(id="flt_x"),
            agents=[EntityRef(id="ag_x")],
            datasets=[EntityRef(id="ds_y")],
        ),
        proposer=ProposerSpec(strategy=ProposerStrategy.GRID),
        run=RunSpec(sandbox=SandboxMode.MOCK),
    )
    return exp.model_dump(mode="json")
