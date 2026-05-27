"""PayloadRouter — keep small payloads inline, push large ones to object store.

The operational spec says payloads > 4KB live in the object store; smaller
ones can be inlined in the Trace JSON. This router decides which is which
and returns a uniform `RoutedPayload` either way.

The router is workspace-scoped: the workspace_id is supplied at construction
and stamped onto every stored pointer.

Encoding rules for `route_value`:
- bytes → measured directly.
- str → utf-8 encoded; length of encoded bytes is the size signal.
- dict / list / scalars → JSON-encoded canonically (sort_keys); size signal
  is the encoded bytes length.

The returned `RoutedPayload` has:
- `inline_value`: the original (small) value, or None if it was offloaded.
- `pointer`: the `oss://...` URI if offloaded, else None.
- `content_hash`: always set; `sha256:...` for both inline and offloaded
  payloads so downstream code can compare without re-hashing.
- `byte_size`: encoded size.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from selfeval._internal.hashing import bytes_hash
from selfeval.storage.interface import ObjectStoreInterface

DEFAULT_INLINE_THRESHOLD_BYTES = 4 * 1024


class PayloadDecision(StrEnum):
    INLINE = "inline"
    POINTER = "pointer"


@dataclass(frozen=True)
class RoutedPayload:
    decision: PayloadDecision
    inline_value: Any | None
    pointer: str | None
    content_hash: str
    byte_size: int


class PayloadRouter:
    def __init__(
        self,
        object_store: ObjectStoreInterface,
        *,
        workspace_id: str,
        inline_threshold_bytes: int = DEFAULT_INLINE_THRESHOLD_BYTES,
    ) -> None:
        if not workspace_id:
            raise ValueError("workspace_id must be non-empty")
        if inline_threshold_bytes < 0:
            raise ValueError("inline_threshold_bytes must be >= 0")
        self._object_store = object_store
        self._workspace_id = workspace_id
        self._threshold = inline_threshold_bytes

    @property
    def threshold_bytes(self) -> int:
        return self._threshold

    def route_value(self, key: str, value: Any) -> RoutedPayload:
        """Route an arbitrary value; encode as needed to measure size."""
        encoded = _encode(value)
        return self._route_bytes(key, value, encoded)

    def route_bytes(self, key: str, data: bytes) -> RoutedPayload:
        """Route raw bytes — caller-controlled encoding."""
        return self._route_bytes(key, data, data)

    def _route_bytes(self, key: str, original: Any, encoded: bytes) -> RoutedPayload:
        size = len(encoded)
        content_hash = bytes_hash(encoded)
        if size <= self._threshold:
            return RoutedPayload(
                decision=PayloadDecision.INLINE,
                inline_value=original,
                pointer=None,
                content_hash=content_hash,
                byte_size=size,
            )
        pointer = self._object_store.put(self._workspace_id, key, encoded)
        return RoutedPayload(
            decision=PayloadDecision.POINTER,
            inline_value=None,
            pointer=pointer,
            content_hash=content_hash,
            byte_size=size,
        )


def _encode(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
