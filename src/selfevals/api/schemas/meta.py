"""Health check and session-token schemas."""

from __future__ import annotations

from pydantic import BaseModel


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
