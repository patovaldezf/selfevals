"""GET /workspaces/{ws}/iterations/{itr}/funnel — the B2 drill-down.

The funnel is persisted on `IterationRecord.metrics.funnel`; this endpoint
reads it directly. We seed an iteration with a real two-level funnel tree and
assert it survives the round-trip, plus the empty and 404 paths.

The same seeded funnel also exercises `experiment_detail`: the reconstructed
`result` now rehydrates the funnel (it used to come back empty there), and
`best_iteration` is surfaced as a first-class field.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from selfevals.api.app import build_app
from selfevals.graders.base import BreakdownNode, GradeLabel
from selfevals.optimization.aggregator import CaseOutcome, aggregate_iteration
from selfevals.schemas.enums import DecisionOutcome, IterationState, ProposerStrategy
from selfevals.schemas.iteration import (
    DecisionRationale,
    DecisionRecord,
    ExecutionInfo,
    IterationDecision,
    IterationMetrics,
    IterationRecord,
    MetricObservation,
    ProposerInputs,
)
from selfevals.storage.seed import seed_workspace
from selfevals.storage.sqlite import SQLiteStorage


def _funnel_metrics() -> dict[str, object]:
    """A populated two-level funnel: `overall` → {retrieval, answer}, with the
    `answer` leaf carrying a failure mode. Reuses the aggregator pattern from
    tests/reporter/test_funnel.py so the CLI and API agree on the same tree."""
    outcome = CaseOutcome(
        case_id="ec_1",
        per_repetition_label=[GradeLabel.PARTIAL],
        per_repetition_score=[0.5],
        breakdowns=[
            BreakdownNode(
                key="overall",
                score=0.5,
                weight=1.0,
                children=[
                    BreakdownNode(key="retrieval", score=1.0, weight=1.0),
                    BreakdownNode(
                        key="answer",
                        score=0.0,
                        weight=1.0,
                        failure_modes=["wrong_answer"],
                    ),
                ],
            )
        ],
    )
    aggregate = aggregate_iteration(case_outcomes=[outcome])
    return {key: node.to_dict() for key, node in aggregate.funnel.items()}


def _iteration(workspace_id: str, *, iteration: int, funnel: dict[str, object]) -> IterationRecord:
    return IterationRecord(
        id=IterationRecord.make_id(),
        workspace_id=workspace_id,
        experiment_id="exp_funnel",
        iteration=iteration,
        state=IterationState.COMPLETED,
        proposer=ProposerInputs(type=ProposerStrategy.MANUAL),
        hypothesis="h",
        proposed_parameters={"x": 1},
        execution=ExecutionInfo(variant_id="var_x"),
        metrics=IterationMetrics(
            primary=MetricObservation(name="pass@1", value=0.5),
            funnel=funnel,
        ),
        decision=IterationDecision(outcome=DecisionOutcome.KEEP_CANDIDATE, rationale="r"),
    )


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db = tmp_path / "selfevals.sqlite"
    st = SQLiteStorage(str(db))
    ws = seed_workspace(st, slug="t", name="t", user_id="local").workspace
    with st.open(ws.id) as scope:
        scope.put_entity(_iteration(ws.id, iteration=3, funnel=_funnel_metrics()))
        scope.put_entity(_iteration(ws.id, iteration=4, funnel={}))
    st.close()
    c = TestClient(build_app(db_path=str(db)))
    c.headers.update({"X-SelfEvals-User": "local"})
    return c


def _iteration_id(client: TestClient, ws_id: str, iteration: int) -> str:
    exp = client.get(f"/api/workspaces/{ws_id}/experiments/exp_funnel/iterations").json()
    [match] = [it for it in exp["iterations"] if it["iteration"] == iteration]
    return str(match["id"])


@pytest.fixture
def ws_id(client: TestClient) -> str:
    return str(client.get("/api/workspaces").json()["workspaces"][0]["id"])


def test_populated_funnel_tree_survives_roundtrip(client: TestClient, ws_id: str) -> None:
    itr = _iteration_id(client, ws_id, 3)
    res = client.get(f"/api/workspaces/{ws_id}/iterations/{itr}/funnel")
    assert res.status_code == 200
    body = res.json()
    assert body["iteration_id"] == itr
    assert body["iteration"] == 3

    root = body["nodes"]["overall"]
    assert root["key"] == "overall"
    # weight-weighted mean of the root node is a float, not null.
    assert isinstance(root["mean_score"], float)

    children = root["children"]
    assert set(children) == {"retrieval", "answer"}
    # The failing leaf carries its failure mode and a 0.0 mean score.
    answer = children["answer"]
    assert answer["failure_mode_counts"] == {"wrong_answer": 1}
    assert answer["mean_score"] == 0.0
    # The passing leaf scored 1.0 and has no failure modes.
    assert children["retrieval"]["mean_score"] == 1.0
    assert children["retrieval"]["failure_mode_counts"] == {}


def test_empty_funnel_returns_empty_nodes(client: TestClient, ws_id: str) -> None:
    # An iteration with no grader breakdown is the pingpong reality: `nodes`
    # is empty and the response is still 200, never an error.
    itr = _iteration_id(client, ws_id, 4)
    res = client.get(f"/api/workspaces/{ws_id}/iterations/{itr}/funnel")
    assert res.status_code == 200
    assert res.json()["nodes"] == {}


def test_unknown_iteration_returns_404(client: TestClient, ws_id: str) -> None:
    res = client.get(f"/api/workspaces/{ws_id}/iterations/itr_does_not_exist/funnel")
    assert res.status_code == 404


# --- experiment detail: best_iteration surfaced + funnel rehydrated in result ---


@pytest.fixture
def detail_client(tmp_path: Path) -> tuple[TestClient, str, str]:
    """A workspace with one experiment and two iterations (one funnel, one not),
    so `experiment_detail` reconstructs a real result. Returns (client, ws_id,
    experiment_id)."""
    from tests.api._experiment_factory import make_experiment

    db = tmp_path / "selfevals.sqlite"
    st = SQLiteStorage(str(db))
    ws = seed_workspace(st, slug="t", name="t", user_id="local").workspace
    exp = make_experiment(workspace_id=ws.id, id="exp_funnel", name="funnel exp")
    with st.open(ws.id) as scope:
        scope.put_entity(exp)
        for i, funnel in ((0, {}), (1, _funnel_metrics())):
            scope.put_entity(_iteration(ws.id, iteration=i, funnel=funnel))
            # `_reconstruct_result` joins iterations to their DecisionRecord by
            # iteration index, skipping any without one — so seed both.
            scope.put_entity(
                DecisionRecord(
                    id=DecisionRecord.make_id(),
                    workspace_id=ws.id,
                    experiment_id=exp.id,
                    iteration=i,
                    variant_id="var_x",
                    outcome=DecisionOutcome.KEEP_CANDIDATE,
                    rationale=DecisionRationale(automated="r"),
                )
            )
    st.close()
    c = TestClient(build_app(db_path=str(db)))
    c.headers.update({"X-SelfEvals-User": "local"})
    return c, ws.id, exp.id


def test_detail_surfaces_best_iteration(detail_client: tuple[TestClient, str, str]) -> None:
    c, ws_id, exp_id = detail_client
    detail = c.get(f"/api/workspaces/{ws_id}/experiments/{exp_id}").json()
    best = detail["best_iteration"]
    assert best is not None
    # Both iterations report primary 0.5 here; best_iteration is a real
    # per-iteration object, not null, and matches the reporter shape.
    assert "iteration" in best
    assert best["metrics"]["primary"]["name"] == "pass@1"


def test_detail_result_rehydrates_funnel(detail_client: tuple[TestClient, str, str]) -> None:
    # Regression: the funnel inside `result.iterations[].funnel` used to always
    # be empty because `_reconstruct_result` did not rehydrate it. Now it does.
    c, ws_id, exp_id = detail_client
    detail = c.get(f"/api/workspaces/{ws_id}/experiments/{exp_id}").json()
    iters = {it["iteration"]: it for it in detail["result"]["iterations"]}
    assert iters[0]["funnel"] == {}  # the iteration we seeded without a funnel
    assert "overall" in iters[1]["funnel"]  # the one with a funnel tree
    assert set(iters[1]["funnel"]["overall"]["children"]) == {"retrieval", "answer"}
