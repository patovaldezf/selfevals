from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from selfevals.api.app import build_app
from selfevals.schemas.enums import SandboxMode, ToolCallStatus, TraceState
from selfevals.schemas.trace import (
    AgentSnapshotRef,
    CostBreakdown,
    EnvironmentInfo,
    FinalState,
    GraderResult,
    LLMCallSpan,
    RunInfo,
    TokenBreakdown,
    ToolCallSpan,
    Trace,
    TraceMetrics,
)
from selfevals.storage.seed import seed_workspace
from selfevals.storage.sqlite import SQLiteStorage

T0 = datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def metrics_client(tmp_path: Path) -> tuple[TestClient, str]:
    db = tmp_path / "metrics.sqlite"
    storage = SQLiteStorage(str(db))
    workspace = seed_workspace(storage, slug="metrics", name="metrics", user_id="local").workspace
    with storage.open(workspace.id) as scope:
        scope.put_entity(
            _trace(
                workspace.id,
                run_id="run_pass",
                started_at=T0,
                label="pass",
                failure_modes=[],
                tool_status=ToolCallStatus.OK,
                tool_error=None,
                retry_chain=[],
                duration_ms=1000,
                cost_usd=0.12,
                input_tokens=100,
                output_tokens=40,
                reasoning_tokens=10,
                ttft_ms=120,
            )
        )
        scope.put_entity(
            _trace(
                workspace.id,
                run_id="run_fail",
                started_at=T0 + timedelta(minutes=5),
                label="fail",
                failure_modes=["missing_tool_call", "bad_final_answer"],
                tool_status=ToolCallStatus.ERROR,
                tool_error="boom",
                retry_chain=["tool_1"],
                duration_ms=3000,
                cost_usd=0.18,
                input_tokens=200,
                output_tokens=80,
                reasoning_tokens=20,
                ttft_ms=300,
            )
        )
    storage.close()
    return TestClient(build_app(db_path=str(db))), workspace.id


def test_metrics_pass_rate_and_failure_modes(metrics_client: tuple[TestClient, str]) -> None:
    client, workspace_id = metrics_client

    pass_rate = client.get(f"/api/workspaces/{workspace_id}/metrics/pass-rate").json()
    assert pass_rate["total"] == 2
    assert {(row["label"], row["count"]) for row in pass_rate["items"]} == {("pass", 1), ("fail", 1)}

    failures = client.get(f"/api/workspaces/{workspace_id}/metrics/failure-modes").json()
    assert failures["total"] == 2
    assert {row["failure_mode"] for row in failures["items"]} == {
        "missing_tool_call",
        "bad_final_answer",
    }


def test_metrics_tools_cost_tokens_and_latency(metrics_client: tuple[TestClient, str]) -> None:
    client, workspace_id = metrics_client

    tools = client.get(f"/api/workspaces/{workspace_id}/metrics/tools").json()
    assert tools["total"] == 2
    by_status = {row["status"]: row for row in tools["items"]}
    assert by_status["ok"]["count"] == 1
    assert by_status["error"]["error_count"] == 1
    assert by_status["error"]["retry_count"] == 1

    cost = client.get(f"/api/workspaces/{workspace_id}/metrics/cost").json()
    assert cost["total"] == 2
    assert cost["items"][0]["provider"] == "openai"
    assert cost["items"][0]["model"] == "gpt-test"
    assert cost["items"][0]["total_cost_usd"] == pytest.approx(0.30)

    tokens = client.get(f"/api/workspaces/{workspace_id}/metrics/tokens").json()
    assert tokens["items"][0]["input_tokens"] == 300
    assert tokens["items"][0]["output_tokens"] == 120
    assert tokens["items"][0]["reasoning_tokens"] == 30
    assert tokens["items"][0]["total_tokens"] == 450

    latency = client.get(f"/api/workspaces/{workspace_id}/metrics/latency").json()
    metrics = {row["metric"]: row for row in latency["items"]}
    assert metrics["trace_duration_ms"]["count"] == 2
    assert metrics["trace_duration_ms"]["p50_ms"] == pytest.approx(2000)
    assert metrics["tool_duration_ms"]["p95_ms"] == pytest.approx(2900)
    assert metrics["ttft_ms"]["p99_ms"] == pytest.approx(298.2)


def test_metrics_filters_by_window_and_experiment(metrics_client: tuple[TestClient, str]) -> None:
    client, workspace_id = metrics_client
    response = client.get(
        f"/api/workspaces/{workspace_id}/metrics/pass-rate",
        params={
            "from": (T0 + timedelta(minutes=1)).isoformat(),
            "experiment_id": "exp_metrics",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["label"] == "fail"


def _trace(
    workspace_id: str,
    *,
    run_id: str,
    started_at: datetime,
    label: str,
    failure_modes: list[str],
    tool_status: ToolCallStatus,
    tool_error: str | None,
    retry_chain: list[str],
    duration_ms: int,
    cost_usd: float,
    input_tokens: int,
    output_tokens: int,
    reasoning_tokens: int,
    ttft_ms: int,
) -> Trace:
    total_tokens = input_tokens + output_tokens + reasoning_tokens
    return Trace(
        id=Trace.make_id(),
        workspace_id=workspace_id,
        run=RunInfo(
            run_id=run_id,
            experiment_id="exp_metrics",
            iteration=0,
            eval_case_id=f"case_{run_id}",
        ),
        agent=AgentSnapshotRef(agent_id="agent", agent_version=1),
        environment=EnvironmentInfo(
            framework_version="test",
            runtime="pytest",
            sandbox=SandboxMode.MOCK,
            started_at=started_at,
            ended_at=started_at + timedelta(milliseconds=duration_ms),
        ),
        final_state=FinalState(status=TraceState.COMPLETED),
        spans=[
            LLMCallSpan(
                id=f"llm_{run_id}",
                name="model call",
                started_at=started_at,
                duration_ms=duration_ms,
                provider="openai",
                model="gpt-test",
                tokens=TokenBreakdown(
                    input=input_tokens,
                    output=output_tokens,
                    reasoning=reasoning_tokens,
                    total=total_tokens,
                ),
                cost_usd=CostBreakdown(total=cost_usd),
                time_to_first_token_ms=ttft_ms,
            ),
            ToolCallSpan(
                id=f"tool_{run_id}",
                name="search",
                started_at=started_at + timedelta(milliseconds=200),
                duration_ms=duration_ms,
                tool_name="search",
                status=tool_status,
                error=tool_error,
                retry_chain=retry_chain,
            ),
        ],
        grader_results=[
            GraderResult(
                grader="judge",
                label=label,
                score=1.0 if label == "pass" else 0.0,
                failure_modes=failure_modes,
            )
        ],
        metrics=TraceMetrics(
            total_tokens_in=input_tokens,
            total_tokens_out=output_tokens,
            total_cost_usd=cost_usd,
            total_duration_ms=duration_ms,
            tool_call_count=1,
            llm_call_count=1,
        ),
    )
