"""Health check, session-token, login, and API-key schemas.

Request emails use pydantic's `EmailStr` (backed by `email-validator`, in the
`web` extra) so malformed addresses are rejected at the edge with a field-level
error. `schemas.user.User` keeps its own check as defence in depth for callers
that bypass the API — the difference is that this one is a real parser rather
than a regex, and it handles the cases a regex reliably gets wrong (consecutive
dots, leading dots, IDNA domains).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from selfevals.schemas.enums import Role


class HealthResponse(BaseModel):
    status: str
    db_path: str
    storage_url: str | None = None
    storage_backend: str = "postgres"


class SessionRequest(BaseModel):
    user_id: str
    ttl_seconds: int = 86_400


class SessionResponse(BaseModel):
    token: str
    user_id: str
    expires_at: int


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=1024)
    display_name: str | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(max_length=1024)


class UserResponse(BaseModel):
    """The caller's own identity. Never carries a hash or a token."""

    id: str
    email: str
    display_name: str | None = None


class CreateApiKeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    role: Role = Role.EXPERIMENTER
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class ApiKeyResponse(BaseModel):
    """A key as listed. `prefix` is the only part of the secret ever returned."""

    id: str
    name: str
    prefix: str
    role: Role
    created_by: str
    created_at: datetime
    expires_at: datetime | None = None
    last_used_at: datetime | None = None


class CreatedApiKeyResponse(ApiKeyResponse):
    """Creation only: carries the plaintext key, shown once and never stored."""

    key: str
