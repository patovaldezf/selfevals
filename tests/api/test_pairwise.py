"""API tests for the pairwise verdict endpoints.

Seed an experiment, then drive the HTTP surface: ingest LLM + human verdicts on
the same pair, list them, and read the calibration report (LLM-vs-human
agreement). The verdict math is `runner.pairwise_ops`'; these pin endpoint
wiring and status codes.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from selfevals.api.app import build_app
from selfevals.runner.launch import ensure_workspace_by_id
from selfevals.storage.factory import open_storage
from tests.api._experiment_factory import make_experiment

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"
EXP = "exp_01HCCCCCCCCCCCCCCCCCCCCCCC"


@pytest.fixture
def client(db_url: str) -> TestClient:
    storage = open_storage(db_url)
    try:
        ensure_workspace_by_id(storage, WS)
        with storage.open(WS) as scope:
            scope.put_entity(make_experiment(workspace_id=WS, id=EXP))
    finally:
        storage.close()
    return TestClient(build_app(db_path=db_url))


def _verdict_body(*, judge_kind: str, judge_id: str, preferred: str, margin: float = 0.5) -> dict:
    return {
        "a_ref": {"kind": "agent_output", "content_snapshot": "out-A"},
        "b_ref": {"kind": "reference", "content_snapshot": "ref-B"},
        "preferred": preferred,
        "margin": margin if preferred != "tie" else 0.0,
        "judge_kind": judge_kind,
        "judge_id": judge_id,
        "rubric_version": 1,
    }


def _url(suffix: str = "") -> str:
    return f"/api/workspaces/{WS}/experiments/{EXP}/verdicts{suffix}"


def test_ingest_and_list(client: TestClient) -> None:
    body = {
        "verdicts": [
            _verdict_body(judge_kind="llm", judge_id="llm:opus", preferred="a"),
            _verdict_body(judge_kind="human", judge_id="human:pato", preferred="a"),
        ]
    }
    res = client.post(_url("/ingest"), json=body)
    assert res.status_code == 200
    assert res.json()["ingested"] == 2

    listed = client.get(_url())
    assert listed.status_code == 200
    assert len(listed.json()) == 2

    humans = client.get(_url(), params={"judge_kind": "human"})
    assert len(humans.json()) == 1
    assert humans.json()[0]["judge_kind"] == "human"


def test_calibration_reports_agreement(client: TestClient) -> None:
    client.post(
        _url("/ingest"),
        json={
            "verdicts": [
                _verdict_body(judge_kind="llm", judge_id="llm:opus", preferred="a"),
                _verdict_body(judge_kind="human", judge_id="human:pato", preferred="a"),
            ]
        },
    )
    report = client.get(_url("/calibration"))
    assert report.status_code == 200
    data = report.json()
    assert data["compared_pairs"] == 1
    assert data["agreements"] == 1
    assert data["agreement_rate"] == pytest.approx(1.0)
    assert data["by_rubric_version"][0]["rubric_version"] == 1


def test_ingest_unknown_experiment_is_422(client: TestClient) -> None:
    url = f"/api/workspaces/{WS}/experiments/exp_missing/verdicts/ingest"
    res = client.post(url, json={"verdicts": [_verdict_body(
        judge_kind="llm", judge_id="llm:opus", preferred="a"
    )]})
    assert res.status_code == 422


def test_ingest_bad_preferred_is_422(client: TestClient) -> None:
    bad = _verdict_body(judge_kind="llm", judge_id="llm:opus", preferred="neither")
    res = client.post(_url("/ingest"), json={"verdicts": [bad]})
    assert res.status_code == 422


def test_invalid_judge_kind_filter_is_422(client: TestClient) -> None:
    res = client.get(_url(), params={"judge_kind": "robot"})
    assert res.status_code == 422


# --- tournaments --------------------------------------------------------

_JUDGE = "tests.api._tournament_judge:judge"


def _tournament_body(strategy: str = "all_pairs", method: str = "elo", **extra: object) -> dict:
    return {
        "candidates": [
            {"id": "a", "output_text": "out-a"},
            {"id": "b", "output_text": "out-b"},
            {"id": "c", "output_text": "out-c"},
        ],
        "judge_entrypoint": _JUDGE,
        "rubric": "which is better?",
        "strategy": strategy,
        "method": method,
        **extra,
    }


def _turl(suffix: str = "") -> str:
    return f"/api/workspaces/{WS}/experiments/{EXP}/tournaments{suffix}"


def test_tournament_runs_and_ranks(client: TestClient) -> None:
    res = client.post(_turl(), json=_tournament_body())
    assert res.status_code == 200
    data = res.json()
    assert data["n_comparisons"] == 3  # all_pairs over 3
    winner = data["ranking"][0]["candidate_id"]
    assert winner == "a"


def test_tournament_persisted_and_listable(client: TestClient) -> None:
    client.post(_turl(), json=_tournament_body(method="bradley_terry"))
    listed = client.get(_turl())
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["method"] == "bradley_terry"


def test_tournament_vs_baseline_is_linear(client: TestClient) -> None:
    res = client.post(_turl(), json=_tournament_body(strategy="vs_baseline", baseline_id="c"))
    assert res.status_code == 200
    assert res.json()["n_comparisons"] == 2


def test_tournament_bad_judge_entrypoint_is_422(client: TestClient) -> None:
    body = _tournament_body()
    body["judge_entrypoint"] = "no_colon_here"
    res = client.post(_turl(), json=body)
    assert res.status_code == 422


def test_tournament_vs_baseline_without_baseline_is_422(client: TestClient) -> None:
    res = client.post(_turl(), json=_tournament_body(strategy="vs_baseline"))
    assert res.status_code == 422
