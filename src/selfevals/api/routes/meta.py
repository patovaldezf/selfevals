"""Health check and session-token issuance."""

from __future__ import annotations

from typing import Annotated

from fastapi import FastAPI, Header

from selfevals.api.auth import OPERATOR_SECRET_HEADER, authorize_operator
from selfevals.api.deps import AppDeps
from selfevals.api.schemas import HealthResponse, SessionRequest, SessionResponse
from selfevals.api.tokens import issue_token
from selfevals.storage.factory import storage_url_label


def register(app: FastAPI, deps: AppDeps) -> None:
    @app.get("/api/health", response_model=HealthResponse, tags=["meta"])
    def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            db_path=storage_url_label(deps.storage_url),
            storage_url=storage_url_label(deps.storage_url),
            storage_backend="postgres",
        )

    @app.post("/api/auth/session", response_model=SessionResponse, tags=["meta"])
    def issue_session(
        payload: SessionRequest,
        operator_secret: Annotated[str | None, Header(alias=OPERATOR_SECRET_HEADER)] = None,
    ) -> SessionResponse:
        authorize_operator(operator_secret)
        token = issue_token(payload.user_id, ttl_seconds=payload.ttl_seconds)
        expires_at = int(token.rsplit(".", 2)[1])
        return SessionResponse(token=token, user_id=payload.user_id, expires_at=expires_at)
