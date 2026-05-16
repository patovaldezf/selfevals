from __future__ import annotations

from pathlib import Path

import pytest

from bootstrap.schemas.enums import FeatureKind
from bootstrap.schemas.registry import FeatureRegistry, RiskProfile
from bootstrap.schemas.workspace import Workspace
from bootstrap.storage.errors import (
    EntityNotFoundError,
    OptimisticConcurrencyError,
    WorkspaceMismatchError,
)
from bootstrap.storage.interface import ListFilter
from bootstrap.storage.sqlite import SQLiteStorage


def _ws(slug: str = "pato") -> Workspace:
    ws_id = Workspace.make_id()
    return Workspace(id=ws_id, workspace_id=ws_id, slug=slug, name="pato")


def _feature(workspace_id: str, *, primary: str = "commerce.product_resolution") -> FeatureRegistry:
    return FeatureRegistry(
        id=FeatureRegistry.make_id(),
        workspace_id=workspace_id,
        kind=FeatureKind.PRODUCT_FEATURE,
        primary_feature=primary,
        description="x",
        default_risk=RiskProfile(overall="medium"),
    )


def test_put_and_get_roundtrip(tmp_path: Path) -> None:
    store = SQLiteStorage(tmp_path / "test.db")
    ws = _ws()
    with store.open(ws.id) as scope:
        scope.put_entity(ws)
        roundtripped = scope.get_entity(Workspace, ws.id)
        assert isinstance(roundtripped, Workspace)
        assert roundtripped.id == ws.id
        assert roundtripped.slug == ws.slug
    store.close()


def test_put_rejects_cross_workspace_entity(tmp_path: Path) -> None:
    store = SQLiteStorage(tmp_path / "test.db")
    ws = _ws()
    other_id = Workspace.make_id()
    foreign = FeatureRegistry(
        id=FeatureRegistry.make_id(),
        workspace_id=other_id,  # belongs to a different workspace
        kind=FeatureKind.PRODUCT_FEATURE,
        primary_feature="x.y",
        description="x",
        default_risk=RiskProfile(overall="low"),
    )
    with store.open(ws.id) as scope, pytest.raises(WorkspaceMismatchError):
        scope.put_entity(foreign)
    store.close()


def test_get_rejects_cross_workspace_read(tmp_path: Path) -> None:
    store = SQLiteStorage(tmp_path / "test.db")
    ws_a = _ws("a")
    ws_b = _ws("b")
    feat = _feature(ws_a.id)
    with store.open(ws_a.id) as scope:
        scope.put_entity(ws_a)
        scope.put_entity(feat)
    with store.open(ws_b.id) as scope, pytest.raises(WorkspaceMismatchError):
        scope.get_entity(FeatureRegistry, feat.id)
    store.close()


def test_get_missing_raises_entity_not_found(tmp_path: Path) -> None:
    store = SQLiteStorage(tmp_path / "test.db")
    ws = _ws()
    with store.open(ws.id) as scope, pytest.raises(EntityNotFoundError):
        scope.get_entity(Workspace, "ws_nope")
    store.close()


def test_optimistic_concurrency_blocks_stale_writes(tmp_path: Path) -> None:
    store = SQLiteStorage(tmp_path / "test.db")
    ws = _ws()
    feat = _feature(ws.id)
    with store.open(ws.id) as scope:
        scope.put_entity(ws)
        scope.put_entity(feat)
        # Update once: v1 -> v2.
        bumped = feat.model_copy(update={"version": 2, "description": "updated"})
        scope.put_entity(bumped)
        # Now writing v4 directly skips a version; raise.
        skipped = feat.model_copy(update={"version": 4})
        with pytest.raises(OptimisticConcurrencyError):
            scope.put_entity(skipped)
    store.close()


def test_idempotent_same_version_write_is_allowed(tmp_path: Path) -> None:
    store = SQLiteStorage(tmp_path / "test.db")
    ws = _ws()
    feat = _feature(ws.id)
    with store.open(ws.id) as scope:
        scope.put_entity(ws)
        scope.put_entity(feat)
        # Re-saving the same version (same payload) is a no-op, not an error.
        scope.put_entity(feat)
    store.close()


def test_list_returns_only_current_workspace(tmp_path: Path) -> None:
    store = SQLiteStorage(tmp_path / "test.db")
    ws_a = _ws("a")
    ws_b = _ws("b")
    with store.open(ws_a.id) as scope:
        scope.put_entity(ws_a)
        scope.put_entity(_feature(ws_a.id, primary="a.one"))
        scope.put_entity(_feature(ws_a.id, primary="a.two"))
    with store.open(ws_b.id) as scope:
        scope.put_entity(ws_b)
        scope.put_entity(_feature(ws_b.id, primary="b.one"))

    with store.open(ws_a.id) as scope:
        items_a = scope.list_entities(FeatureRegistry)
        assert len(items_a) == 2
        assert all(isinstance(i, FeatureRegistry) for i in items_a)
        assert {i.primary_feature for i in items_a} == {"a.one", "a.two"}
    with store.open(ws_b.id) as scope:
        items_b = scope.list_entities(FeatureRegistry)
        assert {i.primary_feature for i in items_b} == {"b.one"}
    store.close()


def test_list_filter_by_payload_field(tmp_path: Path) -> None:
    store = SQLiteStorage(tmp_path / "test.db")
    ws = _ws()
    with store.open(ws.id) as scope:
        scope.put_entity(ws)
        scope.put_entity(_feature(ws.id, primary="commerce.x"))
        scope.put_entity(_feature(ws.id, primary="support.y"))
        items = scope.list_entities(
            FeatureRegistry,
            ListFilter(where={"primary_feature": "support.y"}),
        )
        assert len(items) == 1
        assert items[0].primary_feature == "support.y"
    store.close()


def test_delete_rejects_cross_workspace(tmp_path: Path) -> None:
    store = SQLiteStorage(tmp_path / "test.db")
    ws_a = _ws("a")
    ws_b = _ws("b")
    feat_a = _feature(ws_a.id)
    with store.open(ws_a.id) as scope:
        scope.put_entity(ws_a)
        scope.put_entity(feat_a)
    with store.open(ws_b.id) as scope, pytest.raises(WorkspaceMismatchError):
        scope.delete_entity(FeatureRegistry, feat_a.id)
    with store.open(ws_a.id) as scope:
        scope.delete_entity(FeatureRegistry, feat_a.id)
        assert not scope.exists(FeatureRegistry, feat_a.id)
    store.close()


def test_delete_missing_raises(tmp_path: Path) -> None:
    store = SQLiteStorage(tmp_path / "test.db")
    ws = _ws()
    with store.open(ws.id) as scope, pytest.raises(EntityNotFoundError):
        scope.delete_entity(FeatureRegistry, "ftr_nope")
    store.close()


def test_scope_cannot_be_used_after_close(tmp_path: Path) -> None:
    store = SQLiteStorage(tmp_path / "test.db")
    ws = _ws()
    scope = store.open(ws.id)
    scope.close()
    with pytest.raises(RuntimeError):
        scope.exists(Workspace, ws.id)
    store.close()


def test_in_memory_store_works() -> None:
    store = SQLiteStorage(":memory:")
    ws = _ws()
    with store.open(ws.id) as scope:
        scope.put_entity(ws)
        assert scope.exists(Workspace, ws.id)
    store.close()
