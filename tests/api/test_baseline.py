"""API tests for the baseline + regression endpoints (loop-closer 2B).

Seed a dataset + two completed iterations, then drive the HTTP surface: read
the (missing) baseline, set it from an iteration, and run a regression check of
a worse iteration against it. The math is `ci.regression`'s; these pin the
endpoint wiring and status codes.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from selfevals.api.app import build_app
from selfevals.runner.launch import ensure_workspace_by_id
from selfevals.schemas.dataset import Dataset
from selfevals.schemas.enums import (
    DatasetType,
    DecisionOutcome,
    IterationState,
    ProposerStrategy,
)
from selfevals.schemas.iteration import (
    ExecutionInfo,
    IterationDecision,
    IterationMetrics,
    IterationRecord,
    MetricObservation,
    ProposerInputs,
)
from selfevals.storage.factory import open_storage
from tests.api._experiment_factory import make_experiment

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"
DS = "ds_01HAAAAAAAAAAAAAAAAAAAAAAA"
EXP = "exp_01HCCCCCCCCCCCCCCCCCCCCCCC"
ITR_BASE = "itr_01HBBBBBBBBBBBBBBBBBBBBBB1"
ITR_WORSE = "itr_01HBBBBBBBBBBBBBBBBBBBBBB2"


def _iteration(
    itr_id: str, *, iteration: int, primary: float, f1: dict[str, float | None]
) -> IterationRecord:
    return IterationRecord(
        id=itr_id,
        workspace_id=WS,
        experiment_id=EXP,
        iteration=iteration,
        state=IterationState.COMPLETED,
        proposer=ProposerInputs(type=ProposerStrategy.GRID),
        hypothesis="run",
        execution=ExecutionInfo(variant_id="v0"),
        metrics=IterationMetrics(
            primary=MetricObservation(name="pass@1", value=primary),
            error_rate=0.0,
            confusion={"per_label_f1": f1},
        ),
        decision=IterationDecision(outcome=DecisionOutcome.KEEP_CANDIDATE, rationale="ok"),
    )


@pytest.fixture
def client(db_url: str) -> TestClient:
    storage = open_storage(db_url)
    try:
        ensure_workspace_by_id(storage, WS)
        with storage.open(WS) as scope:
            scope.put_entity(make_experiment(workspace_id=WS, id=EXP))
            scope.put_entity(
                Dataset(id=DS, workspace_id=WS, name="golden", dataset_type=DatasetType.CAPABILITY)
            )
            scope.put_entity(_iteration(ITR_BASE, iteration=0, primary=0.8, f1={"a": 0.9}))
            scope.put_entity(_iteration(ITR_WORSE, iteration=1, primary=0.6, f1={"a": 0.6}))
    finally:
        storage.close()
    return TestClient(build_app(db_path=db_url))


def test_baseline_missing_is_404(client: TestClient) -> None:
    resp = client.get(f"/api/workspaces/{WS}/datasets/{DS}/baseline")
    assert resp.status_code == 404


def test_set_then_get_baseline(client: TestClient) -> None:
    set_resp = client.put(
        f"/api/workspaces/{WS}/datasets/{DS}/baseline", json={"iteration_id": ITR_BASE}
    )
    assert set_resp.status_code == 200, set_resp.text
    body = set_resp.json()
    assert body["iteration_id"] == ITR_BASE
    assert body["primary_metric_value"] == 0.8

    get_resp = client.get(f"/api/workspaces/{WS}/datasets/{DS}/baseline")
    assert get_resp.status_code == 200
    assert get_resp.json()["iteration_id"] == ITR_BASE


def test_set_baseline_requires_iteration(client: TestClient) -> None:
    resp = client.put(f"/api/workspaces/{WS}/datasets/{DS}/baseline", json={"iteration_id": None})
    assert resp.status_code == 422


def test_regression_check_flags_worse_iteration(client: TestClient) -> None:
    client.put(f"/api/workspaces/{WS}/datasets/{DS}/baseline", json={"iteration_id": ITR_BASE})
    resp = client.post(
        f"/api/workspaces/{WS}/datasets/{DS}/regression-check",
        json={"iteration_id": ITR_WORSE},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["regressed"] is True
    assert any(f["regressed"] for f in body["findings"])


def test_regression_check_passes_same_iteration(client: TestClient) -> None:
    client.put(f"/api/workspaces/{WS}/datasets/{DS}/baseline", json={"iteration_id": ITR_BASE})
    resp = client.post(
        f"/api/workspaces/{WS}/datasets/{DS}/regression-check",
        json={"iteration_id": ITR_BASE},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["regressed"] is False


def test_regression_without_baseline_is_404(client: TestClient) -> None:
    resp = client.post(
        f"/api/workspaces/{WS}/datasets/{DS}/regression-check",
        json={"iteration_id": ITR_WORSE},
    )
    assert resp.status_code == 404
