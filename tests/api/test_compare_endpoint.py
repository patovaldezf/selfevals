"""GET .../compare — structured diff over the HTTP bridge (B3).

The endpoint is a thin wrapper over the reporter's `compute_compare`
(the same math the CLI uses). We seed two IterationRecords directly via
SQLiteStorage, then assert the projected `CompareResponse`: a winner
recommendation, correct metric deltas, the honest holdout caveat, and
the 400/404 error paths.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from selfevals.api.app import build_app
from selfevals.schemas.enums import DecisionOutcome, IterationState, ProposerStrategy
from selfevals.schemas.iteration import (
    ExecutionInfo,
    IterationDecision,
    IterationMetrics,
    IterationRecord,
    MetricObservation,
    ProposerInputs,
)
from selfevals.storage.seed import seed_workspace
from selfevals.storage.sqlite import SQLiteStorage

TS = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)


def _iteration_record(
    *,
    workspace_id: str,
    experiment_id: str = "exp_a",
    iteration: int = 0,
    parameters: dict[str, object] | None = None,
    primary: float = 0.5,
    failure_modes: dict[str, int] | None = None,
    outcome: DecisionOutcome = DecisionOutcome.KEEP_CANDIDATE,
) -> IterationRecord:
    return IterationRecord(
        id=IterationRecord.make_id(),
        workspace_id=workspace_id,
        experiment_id=experiment_id,
        iteration=iteration,
        created_at=TS,
        updated_at=TS,
        state=IterationState.COMPLETED,
        proposer=ProposerInputs(type=ProposerStrategy.MANUAL),
        hypothesis="h",
        proposed_parameters=parameters or {"model_params": {"level": 0.0}},
        execution=ExecutionInfo(variant_id="var_x"),
        metrics=IterationMetrics(
            primary=MetricObservation(name="pass@1", value=primary),
            failure_mode_counts=failure_modes or {},
        ),
        decision=IterationDecision(outcome=outcome, rationale="r"),
    )


@pytest.fixture
def seeded(tmp_path: Path) -> tuple[TestClient, str, str, str, str]:
    """Seed a workspace with two iterations of `exp_a` (A loses, B wins) and
    one iteration of a different experiment `exp_b` (for the 400 path)."""
    st = SQLiteStorage(str(tmp_path / "selfevals.sqlite"))
    ws = seed_workspace(st, slug="t", name="t", user_id="local").workspace
    a = _iteration_record(
        workspace_id=ws.id,
        iteration=0,
        parameters={"model_params": {"level": 0.0}},
        primary=0.2,
        failure_modes={"fm_timeout": 3},
        outcome=DecisionOutcome.REJECT,
    )
    b = _iteration_record(
        workspace_id=ws.id,
        iteration=1,
        parameters={"model_params": {"level": 1.0}},
        primary=0.9,
        failure_modes={"fm_new": 2},
        outcome=DecisionOutcome.KEEP_CANDIDATE,
    )
    other = _iteration_record(
        workspace_id=ws.id,
        experiment_id="exp_b",
        iteration=0,
        primary=0.5,
    )
    with st.open(ws.id) as scope:
        scope.put_entity(a)
        scope.put_entity(b)
        scope.put_entity(other)
    st.close()
    client = TestClient(build_app(db_path=str(tmp_path / "selfevals.sqlite")))
    return client, ws.id, a.id, b.id, other.id


def test_compare_winner_b(seeded: tuple[TestClient, str, str, str, str]) -> None:
    client, ws_id, a_id, b_id, _other = seeded
    res = client.get(
        f"/api/workspaces/{ws_id}/experiments/exp_a/compare",
        params={"a": a_id, "b": b_id},
    )
    assert res.status_code == 200
    body = res.json()

    assert body["a_id"] == a_id
    assert body["b_id"] == b_id
    assert body["recommendation"]["kind"] == "winner"
    assert body["recommendation"]["winner"] == "B"
    assert body["recommendation"]["metric_name"] == "pass@1"

    # pass@1 delta is +0.7 (0.9 - 0.2).
    metrics = {row["name"]: row for row in body["metrics_diff"]}
    assert metrics["pass@1"]["a"] == 0.2
    assert metrics["pass@1"]["b"] == 0.9
    assert abs(metrics["pass@1"]["delta"] - 0.7) < 1e-9

    # B introduced a new failure mode; A's mode disappeared.
    fm = body["failure_modes"]
    assert fm["only_a"] == {"fm_timeout": 3}
    assert fm["only_b"] == {"fm_new": 2}
    assert body["recommendation"]["new_failure_modes"] == ["fm_new"]

    # Honest holdout caveat — never a fabricated number.
    assert body["holdout_status"] == "unavailable"


def test_compare_cross_experiment_is_400(
    seeded: tuple[TestClient, str, str, str, str],
) -> None:
    client, ws_id, a_id, _b_id, other_id = seeded
    res = client.get(
        f"/api/workspaces/{ws_id}/experiments/exp_a/compare",
        params={"a": a_id, "b": other_id},
    )
    assert res.status_code == 400


def test_compare_unknown_iteration_is_404(
    seeded: tuple[TestClient, str, str, str, str],
) -> None:
    client, ws_id, a_id, _b_id, _other = seeded
    res = client.get(
        f"/api/workspaces/{ws_id}/experiments/exp_a/compare",
        params={"a": a_id, "b": "itr_01ZZZZZZZZZZZZZZZZZZZZZZZZ"},
    )
    assert res.status_code == 404
