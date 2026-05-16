"""ULID generation (stdlib only).

A ULID is a 128-bit identifier: 48-bit big-endian millisecond timestamp +
80 bits of cryptographic randomness, encoded as 26 chars of Crockford Base32.
Lexicographically sortable by creation time.

Spec: https://github.com/ulid/spec
"""

from __future__ import annotations

import re
import secrets
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford Base32 (no I L O U)
_ALPHABET_SET = frozenset(_ALPHABET)
_ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


def new_ulid() -> str:
    """Generate a new ULID string (26 chars, uppercase Crockford Base32)."""
    timestamp_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    randomness = secrets.randbits(80)
    value = (timestamp_ms << 80) | randomness
    return _encode(value)


def _encode(value: int) -> str:
    chars: list[str] = []
    for _ in range(26):
        chars.append(_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def is_ulid(value: str) -> bool:
    """Return True if `value` is a syntactically valid ULID string."""
    return bool(_ULID_RE.match(value))


def new_prefixed_id(prefix: str) -> str:
    """Generate a prefixed identifier (e.g. `ws_01H...`).

    Prefix must be 2-6 lowercase ASCII letters; separator is underscore.
    """
    if not 2 <= len(prefix) <= 6 or not prefix.isascii() or not prefix.isalpha() or not prefix.islower():
        raise ValueError(f"invalid id prefix: {prefix!r}")
    return f"{prefix}_{new_ulid()}"


_PREFIXED_RE = re.compile(r"^[a-z]{2,6}_[0-9A-HJKMNP-TV-Z]{26}$")


def is_prefixed_id(value: str, prefix: str | None = None) -> bool:
    if not _PREFIXED_RE.match(value):
        return False
    if prefix is None:
        return True
    return value.startswith(f"{prefix}_")
