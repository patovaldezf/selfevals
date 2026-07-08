"""Signed session tokens for ``SELFEVALS_AUTH_MODE=token``.

Tokens are HMAC-SHA256-signed strings of the form
``<user_id>.<expires_at>.<signature>``, verified against
``SELFEVALS_AUTH_SECRET``. No third-party dependency: stdlib ``hmac``/
``hashlib`` cover the signing needs of a single-service internal bridge.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from dataclasses import dataclass

_SEPARATOR = "."


class TokenError(Exception):
    """Raised when a token is missing, malformed, expired, or unsigned correctly."""


def _secret() -> bytes:
    secret = os.environ.get("SELFEVALS_AUTH_SECRET", "").strip()
    if not secret:
        raise TokenError("SELFEVALS_AUTH_SECRET is not configured")
    return secret.encode("utf-8")


def _sign(user_id: str, expires_at: int) -> str:
    payload = f"{user_id}{_SEPARATOR}{expires_at}".encode()
    return hmac.new(_secret(), payload, hashlib.sha256).hexdigest()


def issue_token(user_id: str, *, ttl_seconds: int = 86_400) -> str:
    """Issue a signed token for ``user_id`` valid for ``ttl_seconds``."""
    if not user_id:
        raise TokenError("user_id is required")
    expires_at = int(time.time()) + ttl_seconds
    signature = _sign(user_id, expires_at)
    return f"{user_id}{_SEPARATOR}{expires_at}{_SEPARATOR}{signature}"


@dataclass(frozen=True)
class VerifiedToken:
    user_id: str
    expires_at: int


def verify_token(token: str) -> VerifiedToken:
    """Verify a signed token, raising :class:`TokenError` on any failure."""
    parts = token.split(_SEPARATOR)
    if len(parts) != 3:
        raise TokenError("malformed token")
    user_id, expires_at_raw, signature = parts
    if not user_id:
        raise TokenError("malformed token")
    try:
        expires_at = int(expires_at_raw)
    except ValueError as exc:
        raise TokenError("malformed token") from exc

    expected = _sign(user_id, expires_at)
    if not hmac.compare_digest(expected, signature):
        raise TokenError("invalid signature")
    if expires_at < int(time.time()):
        raise TokenError("token expired")
    return VerifiedToken(user_id=user_id, expires_at=expires_at)
