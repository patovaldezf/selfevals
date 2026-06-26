"""Failure-clusters endpoint (§J.6): GET /workspaces/{ws}/clusters.

v1 clusters by the stable failure-mode slug carried on every grade. These tests
seed traces with overlapping modes plus one taxonomy entry, then assert the
grouping, the example `run_id`s for drill-down, the experiment filter, the
`limit` cap, the empty workspace, and the taxonomy enrichment (`status` from the
taxonomy, `"unknown"` for a mode seen on a grade but absent from it).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from selfevals.api.app import build_app
from selfevals.schemas.enums import FailureModeStatus, SandboxMode, TraceState
from selfevals.schemas.failure_mode import FailureMode
from selfevals.schemas.trace import (
    AgentSnapshotRef,
    EnvironmentInfo,
    FinalState,
    GraderResult,
    RunInfo,
    Trace,
    TraceMetrics,
)
from selfevals.storage.factory import open_storage
from selfevals.storage.seed import seed_workspace

T0 = datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC)


def _trace(
    workspace_id: str,
    *,
    run_id: str,
    started_at: datetime,
    label: str,
    failure_modes: list[str],
    experiment_id: str = "exp_clusters",
) -> Trace:
    return Trace(
        id=Trace.make_id(),
        workspace_id=workspace_id,
        run=RunInfo(
            run_id=run_id,
            experiment_id=experiment_id,
            iteration=0,
            eval_case_id=f"case_{run_id}",
        ),
        agent=AgentSnapshotRef(agent_id="agent", agent_version=1),
        environment=EnvironmentInfo(
            framework_version="test",
            runtime="pytest",
            sandbox=SandboxMode.MOCK,
            started_at=started_at,
            ended_at=started_at + timedelta(milliseconds=1000),
        ),
        final_state=FinalState(status=TraceState.COMPLETED),
        spans=[],
        grader_results=[
            GraderResult(
                grader="judge",
                label=label,
                score=1.0 if label == "pass" else 0.0,
                failure_modes=failure_modes,
            )
        ],
        metrics=TraceMetrics(),
    )


@pytest.fixture
def clusters_client(db_url: str) -> tuple[TestClient, str]:
    storage = open_storage(db_url)
    workspace = seed_workspace(storage, slug="clusters", name="clusters", user_id="local").workspace
    with storage.open(workspace.id) as scope:
        # invented_price appears on two traces; missing_tool_call on one. A
        # passing trace contributes no failure modes.
        scope.put_entity(
            _trace(
                workspace.id,
                run_id="run_a",
                started_at=T0,
                label="fail",
                failure_modes=["invented_price", "missing_tool_call"],
            )
        )
        scope.put_entity(
            _trace(
                workspace.id,
                run_id="run_b",
                started_at=T0 + timedelta(minutes=5),
                label="fail",
                failure_modes=["invented_price"],
                experiment_id="exp_other",
            )
        )
        scope.put_entity(
            _trace(
                workspace.id,
                run_id="run_pass",
                started_at=T0 + timedelta(minutes=10),
                label="pass",
                failure_modes=[],
            )
        )
        # Only invented_price is in the taxonomy (official). missing_tool_call is
        # seen on a grade but never formalized → must read back as "unknown".
        scope.put_entity(
            FailureMode(
                id=FailureMode.make_id(),
                workspace_id=workspace.id,
                slug="invented_price",
                title="Invented price",
                definition="The agent quoted a price that does not exist.",
                status=FailureModeStatus.OFFICIAL,
            )
        )
    storage.close()
    return TestClient(build_app(db_path=db_url)), workspace.id


def test_clusters_group_rank_and_examples(clusters_client: tuple[TestClient, str]) -> None:
    client, workspace_id = clusters_client
    body = client.get(f"/api/workspaces/{workspace_id}/clusters").json()

    # 2 invented_price + 1 missing_tool_call = 3 failing grades.
    assert body["total"] == 3
    items = body["items"]
    assert [row["failure_mode"] for row in items] == ["invented_price", "missing_tool_call"]

    top = items[0]
    assert top["count"] == 2
    assert top["rate"] == pytest.approx(2 / 3)
    # Examples carry the run_id that the trace viewer resolves.
    assert {ex["run_id"] for ex in top["examples"]} == {"run_a", "run_b"}


def test_clusters_enrich_from_taxonomy(clusters_client: tuple[TestClient, str]) -> None:
    client, workspace_id = clusters_client
    items = client.get(f"/api/workspaces/{workspace_id}/clusters").json()["items"]
    by_mode = {row["failure_mode"]: row for row in items}

    # invented_price is registered → title + official status + id.
    assert by_mode["invented_price"]["title"] == "Invented price"
    assert by_mode["invented_price"]["status"] == "official"
    assert by_mode["invented_price"]["failure_mode_id"] is not None

    # missing_tool_call is only seen on a grade → honest "unknown", no id.
    assert by_mode["missing_tool_call"]["status"] == "unknown"
    assert by_mode["missing_tool_call"]["title"] is None
    assert by_mode["missing_tool_call"]["failure_mode_id"] is None


def test_clusters_filter_by_experiment(clusters_client: tuple[TestClient, str]) -> None:
    client, workspace_id = clusters_client
    body = client.get(
        f"/api/workspaces/{workspace_id}/clusters",
        params={"experiment_id": "exp_other"},
    ).json()
    # Only run_b (exp_other), which carries invented_price once.
    assert body["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["failure_mode"] == "invented_price"
    assert body["items"][0]["count"] == 1


def test_clusters_limit_caps_rows(clusters_client: tuple[TestClient, str]) -> None:
    client, workspace_id = clusters_client
    body = client.get(
        f"/api/workspaces/{workspace_id}/clusters",
        params={"limit": 1},
    ).json()
    # Only the top cluster is returned, but total still reflects all failing grades.
    assert len(body["items"]) == 1
    assert body["items"][0]["failure_mode"] == "invented_price"
    assert body["total"] == 3


def test_clusters_empty_workspace(db_url: str) -> None:
    storage = open_storage(db_url)
    workspace = seed_workspace(storage, slug="empty", name="empty", user_id="local").workspace
    storage.close()
    client = TestClient(build_app(db_path=db_url))

    body = client.get(f"/api/workspaces/{workspace.id}/clusters").json()
    assert body["total"] == 0
    assert body["items"] == []
