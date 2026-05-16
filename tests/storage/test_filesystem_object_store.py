from __future__ import annotations

from pathlib import Path

import pytest

from bootstrap._internal.hashing import bytes_hash
from bootstrap.storage.errors import (
    IntegrityViolationError,
    ObjectNotFoundError,
    PointerHashMismatchError,
)
from bootstrap.storage.filesystem import (
    FilesystemObjectStore,
    make_pointer,
    parse_pointer,
)

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def test_put_returns_content_addressed_pointer(tmp_path: Path) -> None:
    store = FilesystemObjectStore(tmp_path)
    p1 = store.put(WS, "system_prompt.txt", b"hello")
    p2 = store.put(WS, "different_key.txt", b"hello")
    # Same bytes → same pointer (content addressing).
    assert p1 == p2
    assert store.get(p1) == b"hello"


def test_pointer_encodes_workspace(tmp_path: Path) -> None:
    store = FilesystemObjectStore(tmp_path)
    pointer = store.put(WS, "k", b"data")
    workspace_id, content_hash = parse_pointer(pointer)
    assert workspace_id == WS
    assert content_hash == bytes_hash(b"data")
    assert store.workspace_for(pointer) == WS


def test_get_missing_raises(tmp_path: Path) -> None:
    store = FilesystemObjectStore(tmp_path)
    missing = make_pointer(WS, bytes_hash(b"never-stored"))
    with pytest.raises(ObjectNotFoundError):
        store.get(missing)


def test_get_detects_tampering(tmp_path: Path) -> None:
    store = FilesystemObjectStore(tmp_path)
    pointer = store.put(WS, "k", b"original")
    workspace_id, content_hash = parse_pointer(pointer)
    hex_part = content_hash.split(":", 1)[1]
    blob_path = tmp_path / workspace_id / hex_part[:2] / f"{hex_part}.bin"
    blob_path.write_bytes(b"tampered")
    with pytest.raises(PointerHashMismatchError):
        store.get(pointer)


def test_exists_returns_false_for_invalid_or_missing_pointers(tmp_path: Path) -> None:
    store = FilesystemObjectStore(tmp_path)
    assert not store.exists("not-a-pointer")
    assert not store.exists(make_pointer(WS, bytes_hash(b"absent")))
    p = store.put(WS, "k", b"present")
    assert store.exists(p)


def test_delete_removes_blob(tmp_path: Path) -> None:
    store = FilesystemObjectStore(tmp_path)
    pointer = store.put(WS, "k", b"x")
    store.delete(pointer)
    assert not store.exists(pointer)
    with pytest.raises(ObjectNotFoundError):
        store.delete(pointer)


def test_put_collision_on_same_hash_different_bytes_raises(tmp_path: Path) -> None:
    """Astronomically rare in practice — but the store must fail loud."""
    store = FilesystemObjectStore(tmp_path)
    pointer = store.put(WS, "k", b"original")
    # Overwrite the file on disk with different bytes; the second put should
    # observe the collision before silently overwriting.
    _, content_hash = parse_pointer(pointer)
    hex_part = content_hash.split(":", 1)[1]
    blob_path = tmp_path / WS / hex_part[:2] / f"{hex_part}.bin"
    blob_path.write_bytes(b"tampered-mismatched")
    with pytest.raises(IntegrityViolationError):
        store.put(WS, "k", b"original")


def test_workspaces_are_isolated_on_disk(tmp_path: Path) -> None:
    store = FilesystemObjectStore(tmp_path)
    p_a = store.put("ws_a", "k", b"data")
    store.put("ws_b", "k", b"data")
    assert (tmp_path / "ws_a").is_dir()
    assert (tmp_path / "ws_b").is_dir()
    # Pointers carry workspace; reading uses path from pointer.
    assert store.get(p_a) == b"data"


def test_clear_workspace(tmp_path: Path) -> None:
    store = FilesystemObjectStore(tmp_path)
    p_a = store.put("ws_a", "k", b"data-a")
    p_b = store.put("ws_b", "k", b"data-b")
    store.clear_workspace("ws_a")
    assert not store.exists(p_a)
    assert store.exists(p_b)


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "oss://",
        "oss://ws/no-hash",
        "oss://ws/sha256:tooshort",
        "https://ws/sha256:" + "a" * 64,
    ],
)
def test_parse_pointer_rejects_bad_strings(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_pointer(bad)


def test_make_pointer_validates_hash_format() -> None:
    with pytest.raises(ValueError):
        make_pointer(WS, "md5:" + "a" * 32)
    with pytest.raises(ValueError):
        make_pointer("", bytes_hash(b"x"))
