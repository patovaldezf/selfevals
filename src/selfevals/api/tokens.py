"""Signed session tokens for ``SELFEVALS_AUTH_MODE=token``.

Tokens are HMAC-SHA256-signed strings of the form ``v2.<payload>.<signature>``,
where ``payload`` is the base64url encoding of ``<user_id>|<expires_at>``,
verified against ``SELFEVALS_AUTH_SECRET``. No third-party dependency: stdlib
``hmac``/``hashlib`` cover the signing needs of a single-service internal
bridge, and avoid JWT's algorithm-confusion footguns.

The v1 format was ``<user_id>.<expires_at>.<signature>`` with the user id
inlined. That made **any user id containing a dot unusable** — which is every
email address, and most OIDC subjects: `issue_token` produced a token that
`verify_token` then rejected as malformed. Encoding the payload fixes it, and
the ``v2.`` prefix means the format can change again without silently
misreading old tokens. v1 tokens are still accepted until they expire (≤24h by
default) so a rollout doesn't log everyone out mid-session.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import time
from dataclasses import dataclass

_SEPARATOR = "."
_FIELD_SEPARATOR = "|"
_VERSION = "v2"


class TokenError(Exception):
    """Raised when a token is missing, malformed, expired, or unsigned correctly."""


def _secret() -> bytes:
    secret = os.environ.get("SELFEVALS_AUTH_SECRET", "").strip()
    if not secret:
        raise TokenError("SELFEVALS_AUTH_SECRET is not configured")
    return secret.encode("utf-8")


def _b64encode(raw: str) -> str:
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def _b64decode(encoded: str) -> str:
    padding = "=" * (-len(encoded) % 4)
    try:
        return base64.urlsafe_b64decode(encoded + padding).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise TokenError("malformed token") from exc


def _sign(payload: str) -> str:
    """Sign the encoded payload. Signing the *encoded* form (not the raw fields)
    keeps the signed bytes unambiguous — no separator can appear inside them."""
    return hmac.new(_secret(), payload.encode("ascii"), hashlib.sha256).hexdigest()


def _sign_v1(user_id: str, expires_at: int) -> str:
    return hmac.new(
        _secret(), f"{user_id}.{expires_at}".encode(), hashlib.sha256
    ).hexdigest()


def issue_token(user_id: str, *, ttl_seconds: int = 86_400) -> str:
    """Issue a signed token for ``user_id`` valid for ``ttl_seconds``."""
    return issue_token_with_expiry(user_id, ttl_seconds=ttl_seconds)[0]


def issue_token_with_expiry(user_id: str, *, ttl_seconds: int = 86_400) -> tuple[str, int]:
    """Issue a token and return it alongside its expiry.

    Callers that need the expiry (the session endpoint reports it) must not
    re-parse the token to recover it: the format is opaque by design, and
    string-slicing it silently broke when the payload became base64.
    """
    if not user_id:
        raise TokenError("user_id is required")
    expires_at = int(time.time()) + ttl_seconds
    payload = _b64encode(f"{user_id}{_FIELD_SEPARATOR}{expires_at}")
    token = f"{_VERSION}{_SEPARATOR}{payload}{_SEPARATOR}{_sign(payload)}"
    return token, expires_at


@dataclass(frozen=True)
class VerifiedToken:
    user_id: str
    expires_at: int


def _verify_v1(token: str) -> VerifiedToken:
    """Verify a legacy ``<user_id>.<expires_at>.<signature>`` token.

    Only reachable for user ids without dots — the ones v1 could round-trip at
    all. Kept so tokens issued before the format change stay valid until expiry.
    """
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
    if not hmac.compare_digest(_sign_v1(user_id, expires_at), signature):
        raise TokenError("invalid signature")
    if expires_at < int(time.time()):
        raise TokenError("token expired")
    return VerifiedToken(user_id=user_id, expires_at=expires_at)


def verify_token(token: str) -> VerifiedToken:
    """Verify a signed token, raising :class:`TokenError` on any failure."""
    if not token.startswith(f"{_VERSION}{_SEPARATOR}"):
        return _verify_v1(token)

    parts = token.split(_SEPARATOR)
    if len(parts) != 3:
        raise TokenError("malformed token")
    _, payload, signature = parts

    # Check the signature before decoding: never parse attacker-controlled bytes
    # we haven't authenticated.
    if not hmac.compare_digest(_sign(payload), signature):
        raise TokenError("invalid signature")

    decoded = _b64decode(payload)
    user_id, sep, expires_at_raw = decoded.rpartition(_FIELD_SEPARATOR)
    if not sep or not user_id:
        raise TokenError("malformed token")
    try:
        expires_at = int(expires_at_raw)
    except ValueError as exc:
        raise TokenError("malformed token") from exc

    if expires_at < int(time.time()):
        raise TokenError("token expired")
    return VerifiedToken(user_id=user_id, expires_at=expires_at)
