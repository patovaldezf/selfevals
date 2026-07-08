"""Aggregate metrics: pass-rate, failure-modes, tools, cost, tokens, latency."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import Depends, FastAPI, Query

from selfevals.api.auth import UserHeader
from selfevals.api.deps import AppDeps
from selfevals.api.metrics import (
    cost_metrics,
    failure_mode_metrics,
    latency_metrics,
    pass_rate_metrics,
    token_metrics,
    tool_metrics,
)
from selfevals.api.schemas import (
    CostMetricsResponse,
    FailureModeMetricsResponse,
    LatencyMetricsResponse,
    PassRateMetricsResponse,
    TokenMetricsResponse,
    ToolMetricsResponse,
)
from selfevals.storage.interface import StorageInterface


def register(app: FastAPI, deps: AppDeps) -> None:
    _register_quality_metrics(app, deps)
    _register_cost_metrics(app, deps)


def _register_quality_metrics(app: FastAPI, deps: AppDeps) -> None:
    @app.get(
        "/api/workspaces/{workspace_id}/metrics/pass-rate",
        response_model=PassRateMetricsResponse,
        tags=["metrics"],
    )
    def metrics_pass_rate(
        workspace_id: str,
        storage: StorageInterface = Depends(deps.storage),
        start: Annotated[datetime | None, Query(alias="from")] = None,
        end: Annotated[datetime | None, Query(alias="to")] = None,
        experiment_id: str | None = None,
        grader: str | None = None,
        _user: UserHeader = None,
    ) -> PassRateMetricsResponse:
        try:
            return pass_rate_metrics(
                storage,
                workspace_id=workspace_id,
                start=start,
                end=end,
                experiment_id=experiment_id,
                grader=grader,
            )
        finally:
            storage.close()

    @app.get(
        "/api/workspaces/{workspace_id}/metrics/failure-modes",
        response_model=FailureModeMetricsResponse,
        tags=["metrics"],
    )
    def metrics_failure_modes(
        workspace_id: str,
        storage: StorageInterface = Depends(deps.storage),
        start: Annotated[datetime | None, Query(alias="from")] = None,
        end: Annotated[datetime | None, Query(alias="to")] = None,
        experiment_id: str | None = None,
        grader: str | None = None,
        _user: UserHeader = None,
    ) -> FailureModeMetricsResponse:
        try:
            return failure_mode_metrics(
                storage,
                workspace_id=workspace_id,
                start=start,
                end=end,
                experiment_id=experiment_id,
                grader=grader,
            )
        finally:
            storage.close()

    @app.get(
        "/api/workspaces/{workspace_id}/metrics/tools",
        response_model=ToolMetricsResponse,
        tags=["metrics"],
    )
    def metrics_tools(
        workspace_id: str,
        storage: StorageInterface = Depends(deps.storage),
        start: Annotated[datetime | None, Query(alias="from")] = None,
        end: Annotated[datetime | None, Query(alias="to")] = None,
        experiment_id: str | None = None,
        tool_name: str | None = None,
        _user: UserHeader = None,
    ) -> ToolMetricsResponse:
        try:
            return tool_metrics(
                storage,
                workspace_id=workspace_id,
                start=start,
                end=end,
                experiment_id=experiment_id,
                tool_name=tool_name,
            )
        finally:
            storage.close()


def _register_cost_metrics(app: FastAPI, deps: AppDeps) -> None:
    @app.get(
        "/api/workspaces/{workspace_id}/metrics/cost",
        response_model=CostMetricsResponse,
        tags=["metrics"],
    )
    def metrics_cost(
        workspace_id: str,
        storage: StorageInterface = Depends(deps.storage),
        start: Annotated[datetime | None, Query(alias="from")] = None,
        end: Annotated[datetime | None, Query(alias="to")] = None,
        experiment_id: str | None = None,
        model: str | None = None,
        _user: UserHeader = None,
    ) -> CostMetricsResponse:
        try:
            return cost_metrics(
                storage,
                workspace_id=workspace_id,
                start=start,
                end=end,
                experiment_id=experiment_id,
                model=model,
            )
        finally:
            storage.close()

    @app.get(
        "/api/workspaces/{workspace_id}/metrics/tokens",
        response_model=TokenMetricsResponse,
        tags=["metrics"],
    )
    def metrics_tokens(
        workspace_id: str,
        storage: StorageInterface = Depends(deps.storage),
        start: Annotated[datetime | None, Query(alias="from")] = None,
        end: Annotated[datetime | None, Query(alias="to")] = None,
        experiment_id: str | None = None,
        model: str | None = None,
        _user: UserHeader = None,
    ) -> TokenMetricsResponse:
        try:
            return token_metrics(
                storage,
                workspace_id=workspace_id,
                start=start,
                end=end,
                experiment_id=experiment_id,
                model=model,
            )
        finally:
            storage.close()

    @app.get(
        "/api/workspaces/{workspace_id}/metrics/latency",
        response_model=LatencyMetricsResponse,
        tags=["metrics"],
    )
    def metrics_latency(
        workspace_id: str,
        storage: StorageInterface = Depends(deps.storage),
        start: Annotated[datetime | None, Query(alias="from")] = None,
        end: Annotated[datetime | None, Query(alias="to")] = None,
        experiment_id: str | None = None,
        _user: UserHeader = None,
    ) -> LatencyMetricsResponse:
        try:
            return latency_metrics(
                storage,
                workspace_id=workspace_id,
                start=start,
                end=end,
                experiment_id=experiment_id,
            )
        finally:
            storage.close()
