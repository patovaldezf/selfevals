"""Content hashing for snapshot identity and pointer integrity."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def content_hash(payload: Any) -> str:
    """Return a stable `sha256:...` hash of a JSON-serializable payload.

    Keys are sorted and separators are canonical to make the hash reproducible
    regardless of dict insertion order.
    """
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    return f"sha256:{digest}"


def bytes_hash(data: bytes) -> str:
    """Return `sha256:...` for raw bytes (used for stored blobs)."""
    return f"sha256:{hashlib.sha256(data).hexdigest()}"
