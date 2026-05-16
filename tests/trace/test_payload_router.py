from __future__ import annotations

from pathlib import Path

import pytest

from bootstrap.storage.filesystem import FilesystemObjectStore
from bootstrap.trace.payload_router import (
    DEFAULT_INLINE_THRESHOLD_BYTES,
    PayloadDecision,
    PayloadRouter,
)

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def test_default_threshold_is_4kb() -> None:
    assert DEFAULT_INLINE_THRESHOLD_BYTES == 4096


def test_small_value_stays_inline(tmp_path: Path) -> None:
    router = PayloadRouter(FilesystemObjectStore(tmp_path), workspace_id=WS)
    r = router.route_value("system_prompt", "hello")
    assert r.decision == PayloadDecision.INLINE
    assert r.inline_value == "hello"
    assert r.pointer is None
    assert r.content_hash.startswith("sha256:")
    assert r.byte_size == len(b"hello")


def test_large_value_goes_to_object_store(tmp_path: Path) -> None:
    store = FilesystemObjectStore(tmp_path)
    router = PayloadRouter(store, workspace_id=WS, inline_threshold_bytes=100)
    big = "x" * 500
    r = router.route_value("transcript", big)
    assert r.decision == PayloadDecision.POINTER
    assert r.inline_value is None
    assert r.pointer is not None
    assert store.exists(r.pointer)
    assert store.get(r.pointer) == big.encode()


def test_route_dict_canonicalized(tmp_path: Path) -> None:
    router = PayloadRouter(FilesystemObjectStore(tmp_path), workspace_id=WS)
    r1 = router.route_value("msgs", {"a": 1, "b": 2})
    r2 = router.route_value("msgs", {"b": 2, "a": 1})
    # Same canonical bytes ⇒ same hash, both inline.
    assert r1.content_hash == r2.content_hash
    assert r1.decision == PayloadDecision.INLINE


def test_route_bytes_skips_encoding(tmp_path: Path) -> None:
    router = PayloadRouter(FilesystemObjectStore(tmp_path), workspace_id=WS)
    r = router.route_bytes("raw", b"\x00\x01\x02\x03")
    assert r.decision == PayloadDecision.INLINE
    assert r.inline_value == b"\x00\x01\x02\x03"
    assert r.byte_size == 4


def test_threshold_boundary_is_inclusive(tmp_path: Path) -> None:
    router = PayloadRouter(
        FilesystemObjectStore(tmp_path),
        workspace_id=WS,
        inline_threshold_bytes=10,
    )
    # exactly at threshold → inline
    r = router.route_value("k", "x" * 10)
    assert r.decision == PayloadDecision.INLINE
    # one over → pointer
    r2 = router.route_value("k", "x" * 11)
    assert r2.decision == PayloadDecision.POINTER


def test_workspace_id_required(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        PayloadRouter(FilesystemObjectStore(tmp_path), workspace_id="")


def test_negative_threshold_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        PayloadRouter(
            FilesystemObjectStore(tmp_path), workspace_id=WS, inline_threshold_bytes=-1
        )
