"""User and UserSession: identity, which is the one thing that isn't tenanted.

Every other entity is scoped to a workspace and inherits `BaseEntity`. A user is
not: the same person holds memberships in several workspaces, so hanging them
off one would make identity a property of whichever tenant happened to create
them. These models therefore build on `SelfEvalsModel` and carry their own id /
timestamps rather than inheriting a `workspace_id` they would have to fake.

Authorization is unchanged: `members` still decides who may read or write what.
This is authentication — *who* the caller is, separate from *what they may do*.
"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

from pydantic import EmailStr, Field, field_validator

from selfevals._internal.ids import new_prefixed_id
from selfevals._internal.time import utc_now
from selfevals.schemas._base import NonEmptyStr, SelfEvalsModel


class User(SelfEvalsModel):
    """A person who can authenticate. Global, not workspace-scoped."""

    _id_prefix: ClassVar[str] = "usr"

    id: NonEmptyStr = Field(default_factory=lambda: new_prefixed_id("usr"))
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    deleted_at: datetime | None = None

    # EmailStr is a real parser, not a pattern: it rejects the shapes a regex
    # reliably lets through (`a..b@x.com`, `.a@x.com`, `a@-x.com`) and handles
    # IDNA domains. Backed by `email-validator`, which ships with the `web`
    # extra alongside the API this identity exists to authenticate against.
    email: EmailStr
    display_name: str | None = None
    # None means this identity has no password login (SSO-only, or API-only).
    # Never the password itself — hashing happens before construction.
    password_hash: str | None = None
    last_login_at: datetime | None = None

    @staticmethod
    def make_id() -> str:
        return new_prefixed_id("usr")

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        """Lowercase for storage, matching the `lower(email)` unique index.

        Validation already happened — `EmailStr` runs first. This only settles
        casing so `Ada@Example.com` and `ada@example.com` cannot become two
        accounts.
        """
        return value.strip().lower()

    def is_active(self) -> bool:
        return self.deleted_at is None


class UserSession(SelfEvalsModel):
    """One issued login session, so a token can be revoked individually.

    Tokens are stateless HMAC: without a row like this, "log out" can only be
    implemented by rotating the signing secret, which signs everyone out
    everywhere. The session id travels inside the token, so verification can
    check this row and honour a revocation.
    """

    _id_prefix: ClassVar[str] = "ses"

    id: NonEmptyStr = Field(default_factory=lambda: new_prefixed_id("ses"))
    user_id: NonEmptyStr
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime
    revoked_at: datetime | None = None
    # Best-effort provenance for a "signed in on N devices" view; proxies may
    # not forward either.
    user_agent: str | None = None
    ip_address: str | None = None

    @staticmethod
    def make_id() -> str:
        return new_prefixed_id("ses")

    def is_live(self, *, now: datetime | None = None) -> bool:
        """Usable right now: neither revoked nor expired."""
        moment = now or utc_now()
        return self.revoked_at is None and self.expires_at > moment
