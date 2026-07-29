"""Password hashing, session tokens, and API keys.

Three primitives, each with a different threat model — conflating them is how
auth code goes wrong:

* **Passwords** are low-entropy and chosen by humans, so they need a slow,
  memory-hard KDF (Argon2id) to make offline cracking expensive.
* **Session tokens** and **API keys** are 256 bits of CSPRNG output. There is
  nothing to brute-force, so a fast SHA-256 is the correct choice: it keeps
  verification to one indexed lookup while still meaning a database dump hands
  over no usable credentials. Running Argon2 here would add latency to every
  request and buy nothing.

Nothing in this module touches storage or FastAPI — it is pure functions over
strings, so it can be tested exhaustively without a database.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from argon2 import PasswordHasher

# 32 bytes → 256 bits, comfortably above OWASP's 64-bit session-id floor.
_TOKEN_BYTES = 32
_API_KEY_BYTES = 32
# Identifies our keys in logs and secret scanners, and tells a user at a glance
# what they pasted. Mirrors the convention Stripe/Langfuse popularized.
API_KEY_PREFIX = "sk_se_"
# Enough of the key to disambiguate in a list without being usable.
_DISPLAY_PREFIX_LEN = len(API_KEY_PREFIX) + 6


class PasswordHashUnavailableError(RuntimeError):
    """Raised when password auth is used without the `web` extra installed."""


def hash_password(password: str) -> str:
    """Hash a password with Argon2id using the library's current defaults.

    Deliberately not parameterized: pinning cost factors here would freeze them
    at today's hardware. `argon2-cffi` tracks the RFC 9106 recommendations, and
    `needs_rehash` (below) lets stored hashes migrate as those defaults rise.
    """
    hasher = _password_hasher()
    return str(hasher.hash(password))


def verify_password(password: str, stored_hash: str) -> bool:
    """Check a password against a stored hash. False on any mismatch.

    Returns False rather than raising for a wrong password — a caller should not
    have to distinguish "wrong" from "malformed" to answer "let them in?".
    """
    from argon2.exceptions import VerificationError, VerifyMismatchError

    hasher = _password_hasher()
    try:
        return bool(hasher.verify(stored_hash, password))
    except (VerifyMismatchError, VerificationError):
        return False
    except Exception:  # audit:ignore[broad_exception_catches] — a corrupt hash must read as "denied", never as an error page
        return False


def needs_rehash(stored_hash: str) -> bool:
    """Whether a stored hash used weaker parameters than today's defaults.

    Call after a successful verify: that is the only moment the plaintext is in
    hand, so it is the only chance to upgrade the stored hash in place.
    """
    try:
        return bool(_password_hasher().check_needs_rehash(stored_hash))
    except Exception:  # audit:ignore[broad_exception_catches] — an unreadable hash is not a reason to fail a login that already verified
        return False


def _password_hasher() -> PasswordHasher:
    try:
        from argon2 import PasswordHasher as _PasswordHasher
    except ImportError as exc:  # pragma: no cover - exercised by the extras matrix
        raise PasswordHashUnavailableError(
            "password authentication needs argon2-cffi: pip install 'selfevals[web]'"
        ) from exc
    return _PasswordHasher()


def new_session_token() -> str:
    """Mint an opaque session token for the login cookie."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def hash_token(token: str) -> str:
    """Hash a high-entropy token for storage and lookup.

    Deterministic (unsalted) on purpose: the token *is* the lookup key, so the
    hash has to be reproducible from the value the client presents.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_match(presented: str, stored_hash: str) -> bool:
    """Constant-time comparison of a presented token against a stored hash."""
    return hmac.compare_digest(hash_token(presented), stored_hash)


def new_api_key() -> tuple[str, str, str]:
    """Mint an API key. Returns ``(plaintext, prefix, hash)``.

    The plaintext is returned once, to be shown to the user and never persisted;
    the prefix is stored in clear so a key list can be readable.
    """
    secret = secrets.token_urlsafe(_API_KEY_BYTES)
    plaintext = f"{API_KEY_PREFIX}{secret}"
    return plaintext, plaintext[:_DISPLAY_PREFIX_LEN], hash_token(plaintext)


def looks_like_api_key(value: str) -> bool:
    """Whether a credential is an API key rather than a session token.

    Lets one auth dependency route a request to the right lane without trying
    both lookups against the database.
    """
    return value.startswith(API_KEY_PREFIX)
