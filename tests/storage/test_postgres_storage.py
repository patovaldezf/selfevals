"""Contract tests for the Postgres storage backend.

These exercise the generic ``WorkspaceScope`` contract (put/get/list/delete/
exists, workspace isolation, optimistic concurrency) against a real Postgres
database via the ``storage`` fixture (a fresh, isolated per-test database).
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import pytest

from selfevals.schemas.enums import FeatureKind
from selfevals.schemas.registry import FeatureRegistry, RiskProfile
from selfevals.schemas.workspace import Workspace
from selfevals.storage.errors import (
    EntityNotFoundError,
    OptimisticConcurrencyError,
    WorkspaceMismatchError,
)
from selfevals.storage.interface import ListFilter

if TYPE_CHECKING:
    from selfevals.storage.interface import StorageInterface


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


def test_put_and_get_roundtrip(storage: StorageInterface) -> None:
    ws = _ws()
    with storage.open(ws.id) as scope:
        scope.put_entity(ws)
        roundtripped = scope.get_entity(Workspace, ws.id)
        assert isinstance(roundtripped, Workspace)
        assert roundtripped.id == ws.id
        assert roundtripped.slug == ws.slug


def test_put_rejects_cross_workspace_entity(storage: StorageInterface) -> None:
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
    with storage.open(ws.id) as scope, pytest.raises(WorkspaceMismatchError):
        scope.put_entity(foreign)


def test_get_rejects_cross_workspace_read(storage: StorageInterface) -> None:
    ws_a = _ws("a")
    ws_b = _ws("b")
    feat = _feature(ws_a.id)
    with storage.open(ws_a.id) as scope:
        scope.put_entity(ws_a)
        scope.put_entity(feat)
    with storage.open(ws_b.id) as scope:
        scope.put_entity(ws_b)
    with storage.open(ws_b.id) as scope, pytest.raises(WorkspaceMismatchError):
        scope.get_entity(FeatureRegistry, feat.id)


def test_get_missing_raises_entity_not_found(storage: StorageInterface) -> None:
    ws = _ws()
    with storage.open(ws.id) as scope, pytest.raises(EntityNotFoundError):
        scope.get_entity(Workspace, "ws_nope")


def test_optimistic_concurrency_blocks_stale_writes(storage: StorageInterface) -> None:
    ws = _ws()
    feat = _feature(ws.id)
    with storage.open(ws.id) as scope:
        scope.put_entity(ws)
        scope.put_entity(feat)
        # Update once: v1 -> v2.
        bumped = feat.model_copy(update={"version": 2, "description": "updated"})
        scope.put_entity(bumped)
        # Now writing v4 directly skips a version; raise.
        skipped = feat.model_copy(update={"version": 4})
        with pytest.raises(OptimisticConcurrencyError):
            scope.put_entity(skipped)


def test_idempotent_same_version_write_is_allowed(storage: StorageInterface) -> None:
    ws = _ws()
    feat = _feature(ws.id)
    with storage.open(ws.id) as scope:
        scope.put_entity(ws)
        scope.put_entity(feat)
        # Re-saving the same version (same payload) is a no-op, not an error.
        scope.put_entity(feat)


def test_list_returns_only_current_workspace(storage: StorageInterface) -> None:
    ws_a = _ws("a")
    ws_b = _ws("b")
    with storage.open(ws_a.id) as scope:
        scope.put_entity(ws_a)
        scope.put_entity(_feature(ws_a.id, primary="a.one"))
        scope.put_entity(_feature(ws_a.id, primary="a.two"))
    with storage.open(ws_b.id) as scope:
        scope.put_entity(ws_b)
        scope.put_entity(_feature(ws_b.id, primary="b.one"))

    with storage.open(ws_a.id) as scope:
        items_a = scope.list_entities(FeatureRegistry)
        assert len(items_a) == 2
        assert all(isinstance(i, FeatureRegistry) for i in items_a)
        assert {i.primary_feature for i in items_a} == {"a.one", "a.two"}
    with storage.open(ws_b.id) as scope:
        items_b = scope.list_entities(FeatureRegistry)
        assert {i.primary_feature for i in items_b} == {"b.one"}


def test_list_filter_by_column(storage: StorageInterface) -> None:
    ws = _ws()
    with storage.open(ws.id) as scope:
        scope.put_entity(ws)
        scope.put_entity(_feature(ws.id, primary="commerce.x"))
        scope.put_entity(_feature(ws.id, primary="support.y"))
        items = scope.list_entities(
            FeatureRegistry,
            ListFilter(where={"primary_feature": "support.y"}),
        )
        assert len(items) == 1
        assert items[0].primary_feature == "support.y"


def test_list_rejects_untrusted_order_by(storage: StorageInterface) -> None:
    ws = _ws()
    with storage.open(ws.id) as scope:
        scope.put_entity(ws)
        with pytest.raises(ValueError, match="unsupported order_by"):
            scope.list_entities(
                FeatureRegistry,
                ListFilter(order_by="created_at; DROP TABLE feature_registries"),
            )


def test_delete_rejects_cross_workspace(storage: StorageInterface) -> None:
    ws_a = _ws("a")
    ws_b = _ws("b")
    feat_a = _feature(ws_a.id)
    with storage.open(ws_a.id) as scope:
        scope.put_entity(ws_a)
        scope.put_entity(feat_a)
    with storage.open(ws_b.id) as scope:
        scope.put_entity(ws_b)
    with storage.open(ws_b.id) as scope, pytest.raises(WorkspaceMismatchError):
        scope.delete_entity(FeatureRegistry, feat_a.id)
    with storage.open(ws_a.id) as scope:
        scope.delete_entity(FeatureRegistry, feat_a.id)
        assert not scope.exists(FeatureRegistry, feat_a.id)


def test_delete_missing_raises(storage: StorageInterface) -> None:
    ws = _ws()
    with storage.open(ws.id) as scope, pytest.raises(EntityNotFoundError):
        scope.delete_entity(FeatureRegistry, "ftr_nope")


def test_scope_cannot_be_used_after_close(storage: StorageInterface) -> None:
    ws = _ws()
    scope = storage.open(ws.id)
    scope.close()
    with pytest.raises(RuntimeError):
        scope.exists(Workspace, ws.id)


def test_transaction_rolls_back_on_error(storage: StorageInterface) -> None:
    ws = _ws()
    with storage.open(ws.id) as scope:
        scope.put_entity(ws)
    with pytest.raises(RuntimeError), storage.transaction():  # type: ignore[attr-defined]
        with storage.open(ws.id) as scope:
            scope.put_entity(_feature(ws.id, primary="rollback.me"))
        raise RuntimeError("boom")
    with storage.open(ws.id) as scope:
        assert scope.list_entities(FeatureRegistry) == []


def test_storage_usable_across_threads(db_url: str) -> None:
    """FastAPI runs sync handlers in a threadpool; a connection may be opened on
    one worker and read/closed on another. psycopg connections are guarded by an
    internal lock, so a single connection must be usable across threads."""
    from selfevals.storage.factory import open_storage

    store = open_storage(db_url)
    ws = _ws()
    with store.open(ws.id) as scope:
        scope.put_entity(ws)

    out: dict[str, object] = {}

    def _read() -> None:
        try:
            with store.open(ws.id) as scope:
                out["found"] = scope.exists(Workspace, ws.id)
        except Exception as e:  # pragma: no cover - surfaced via assert
            out["error"] = repr(e)

    t = threading.Thread(target=_read)
    t.start()
    t.join(timeout=5.0)
    assert "error" not in out, f"cross-thread read failed: {out.get('error')!r}"
    assert out.get("found") is True
    store.close()
