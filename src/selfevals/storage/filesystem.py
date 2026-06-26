"""Filesystem object store implementation.

Layout under `root_path`:

    {workspace_id}/{prefix2}/{content_hash_hex}.bin

where `prefix2` is the first two hex chars of the content hash (Git-style
fan-out so a single directory never holds a million files).

Pointers are stable opaque URIs:

    oss://{workspace_id}/{content_hash}

A pointer encodes its workspace, so `workspace_for(pointer)` is trivial.
This is what `WorkspaceScope` checks before allowing a cross-workspace
read of a payload via a pointer obtained elsewhere.

Storage is content-addressed: writing the same bytes twice resolves to
the same pointer. The object store tracks the human-readable `key`
separately and is kept independent of the row store — it does not need a
database to function.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from selfevals._internal.hashing import bytes_hash
from selfevals.storage.errors import (
    IntegrityViolationError,
    ObjectNotFoundError,
    PointerHashMismatchError,
)
from selfevals.storage.interface import ObjectStoreInterface

_POINTER_SCHEME = "oss://"
_WORKSPACE_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_POINTER_RE = re.compile(r"^oss://(?P<workspace>[A-Za-z0-9._-]+)/(?P<hash>sha256:[0-9a-f]{64})$")


def _validate_workspace_id(workspace_id: str) -> None:
    if not workspace_id:
        raise ValueError("workspace_id must be non-empty")
    if not _WORKSPACE_RE.fullmatch(workspace_id):
        raise ValueError(
            "workspace_id may only contain ASCII letters, digits, dot, underscore, and hyphen"
        )


def make_pointer(workspace_id: str, content_hash: str) -> str:
    _validate_workspace_id(workspace_id)
    if not content_hash.startswith("sha256:") or len(content_hash) != len("sha256:") + 64:
        raise ValueError(f"content_hash must be sha256:<64-hex>, got {content_hash!r}")
    return f"{_POINTER_SCHEME}{workspace_id}/{content_hash}"


def parse_pointer(pointer: str) -> tuple[str, str]:
    """Return (workspace_id, content_hash) for a valid pointer."""
    m = _POINTER_RE.match(pointer)
    if m is None:
        raise ValueError(f"invalid object pointer: {pointer!r}")
    return m.group("workspace"), m.group("hash")


class FilesystemObjectStore(ObjectStoreInterface):
    def __init__(self, root_path: str | Path) -> None:
        self._root = Path(root_path)
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def put(self, workspace_id: str, key: str, data: bytes) -> str:
        _validate_workspace_id(workspace_id)
        if not key:
            raise ValueError("key must be non-empty")
        content_hash = bytes_hash(data)
        path = self._path_for(workspace_id, content_hash)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            existing = path.read_bytes()
            if existing != data:
                # SHA256 collision — astronomically unlikely; fail loud.
                raise IntegrityViolationError(f"content_hash collision on {content_hash}")
            return make_pointer(workspace_id, content_hash)
        path.write_bytes(data)
        return make_pointer(workspace_id, content_hash)

    def get(self, pointer: str) -> bytes:
        workspace_id, content_hash = parse_pointer(pointer)
        path = self._path_for(workspace_id, content_hash)
        if not path.exists():
            raise ObjectNotFoundError(pointer)
        data = path.read_bytes()
        actual = bytes_hash(data)
        if actual != content_hash:
            raise PointerHashMismatchError(pointer, content_hash, actual)
        return data

    def exists(self, pointer: str) -> bool:
        try:
            workspace_id, content_hash = parse_pointer(pointer)
        except ValueError:
            return False
        return self._path_for(workspace_id, content_hash).exists()

    def delete(self, pointer: str) -> None:
        workspace_id, content_hash = parse_pointer(pointer)
        path = self._path_for(workspace_id, content_hash)
        if not path.exists():
            raise ObjectNotFoundError(pointer)
        path.unlink()

    def workspace_for(self, pointer: str) -> str:
        workspace_id, _ = parse_pointer(pointer)
        return workspace_id

    def clear_workspace(self, workspace_id: str) -> None:
        """Remove all blobs for a workspace. Used by tests / retention."""
        _validate_workspace_id(workspace_id)
        ws_dir = self._root / workspace_id
        if ws_dir.exists():
            shutil.rmtree(ws_dir)

    def _path_for(self, workspace_id: str, content_hash: str) -> Path:
        _validate_workspace_id(workspace_id)
        # content_hash is `sha256:<64 hex>` — fan out by first 2 hex chars.
        hex_part = content_hash.split(":", 1)[1]
        path = self._root / workspace_id / hex_part[:2] / f"{hex_part}.bin"
        resolved_root = self._root.resolve()
        resolved_path = path.resolve()
        if resolved_root != resolved_path and resolved_root not in resolved_path.parents:
            raise ValueError("object path escapes store root")
        return path
