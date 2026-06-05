"""GET .../experiments/{id}/results — per-scenario expected/detected/matched.

Runs a tiny real experiment (offline: mock sandbox, embedded agent that echoes
"pong", deterministic grader) with two cases — one that passes and one that
fails — persisting every trace, then asserts the results grid carries, per case:
the expectation, what was produced, the pass/fail verdict, the failure modes,
and a resolvable run_id/trace_id. This is the fix for
"best_iteration.failure_reasons dice que falló pero no cuál caso".
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from selfevals.api.app import build_app
from selfevals.graders.deterministic import DeterministicGrader
from selfevals.optimization.loop import OptimizationLoop
from selfevals.optimization.proposers import GridProposer
from selfevals.runner.adapters import AdapterRequest, AdapterResponse, EmbeddedAdapter
from selfevals.runner.executor import Executor
from selfevals.runner.launch import payload_router_for_db
from selfevals.runner.sandbox import SandboxPolicy
from selfevals.schemas.enums import (
    AgentType,
    DatasetSource,
    DatasetType,
    GroundTruthMethod,
    Level,
    SandboxMode,
)
from selfevals.schemas.eval_case import (
    CaseTaxonomy,
    EvalCase,
    Expected,
    FeatureTag,
    GroundTruthSpec,
    SourceInfo,
)
from selfevals.schemas.experiment import SearchSpace
from selfevals.schemas.fleet import Agent, ModelRef
from selfevals.storage.seed import seed_workspace
from selfevals.storage.sqlite import SQLiteStorage

from ._experiment_factory import make_experiment


def _agent(ws_id: str) -> Agent:
    return Agent(
        id=Agent.make_id(),
        workspace_id=ws_id,
        agent_type=AgentType.SYSTEM_PROMPT,
        model=ModelRef(provider="anthropic", name="claude-sonnet-4-6"),
        system_prompt_pointer="oss://prompts/x",
    )


def _case(name: str, must_include: str, *, ws_id: str, experiment_id: str) -> EvalCase:
    return EvalCase(
        id=EvalCase.make_id(),
        workspace_id=ws_id,
        experiment_id=experiment_id,
        name=name,
        task_type="echo",
        input={"messages": [{"role": "user", "content": "ping"}]},
        taxonomy=CaseTaxonomy(
            level=Level.FINAL_RESPONSE,
            feature=FeatureTag(primary="commerce.product_resolution"),
            source=SourceInfo(type=DatasetSource.HANDCRAFTED),
            ground_truth=GroundTruthSpec(methods=[GroundTruthMethod.EXACT_MATCH]),
            dataset_type=DatasetType.CAPABILITY,
        ),
        expected=Expected(must_include=[must_include]),
    )


def _pong(req: AdapterRequest) -> AdapterResponse:
    return AdapterResponse(content="pong", tokens_input=4, tokens_output=2, stop_reason="end_turn")


@pytest.fixture
def seeded(tmp_path: Path) -> tuple[TestClient, str, str]:
    """A completed experiment with one passing + one failing case, all traces
    persisted. Returns (client, workspace_id, experiment_id)."""
    db = tmp_path / "selfevals.sqlite"
    storage = SQLiteStorage(str(db))
    ws = seed_workspace(storage, slug="t", name="t", user_id="local").workspace
    ws_id = ws.id
    exp = make_experiment(workspace_id=ws_id, name="results-exp")
    exp.run.persist_traces = "all"
    exp.run.sandbox = SandboxMode.MOCK
    exp.run.max_iterations = 1
    # GridProposer needs at least one combination; the embedded agent ignores it.
    exp.search_space = SearchSpace(model_params={"level": [1.0]})
    # "pong" passes (agent emits pong), "zzz" fails the must_include rule.
    pass_case = _case("say pong", "pong", ws_id=ws_id, experiment_id=exp.id)
    fail_case = _case("say zzz", "zzz", ws_id=ws_id, experiment_id=exp.id)
    with storage.open(ws_id) as scope:
        scope.put_entity(exp)
        scope.put_entity(pass_case)
        scope.put_entity(fail_case)
    scope = storage.open(ws_id)
    executor = Executor(
        adapter=EmbeddedAdapter(_pong, agent=_agent(ws_id)),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=ws_id,
        payload_router=payload_router_for_db(str(db), ws_id),
    )
    loop = OptimizationLoop(
        experiment=exp,
        executor=executor,
        proposer=GridProposer(),
        graders=[DeterministicGrader()],
        cases=[pass_case, fail_case],
        scope=scope,
    )
    import asyncio

    asyncio.run(loop.run())
    scope.close()
    storage.close()

    c = TestClient(build_app(db_path=str(db)))
    c.headers.update({"X-SelfEvals-User": "local"})
    return c, ws_id, exp.id


def test_results_grid_carries_expected_detected_matched(
    seeded: tuple[TestClient, str, str],
) -> None:
    c, ws_id, exp_id = seeded
    res = c.get(f"/api/workspaces/{ws_id}/experiments/{exp_id}/results")
    assert res.status_code == 200
    body = res.json()
    assert body["experiment_id"] == exp_id
    assert body["iteration"] is not None
    assert body["total"] == 2

    by_name = {row["case_name"]: row for row in body["cases"]}
    assert set(by_name) == {"say pong", "say zzz"}

    passed = by_name["say pong"]
    assert passed["matched"] is True
    assert passed["expected"]["must_include"] == ["pong"]
    assert passed["detected"]["content"] == "pong"
    assert passed["failure_modes"] == []
    # Resolvable trace reference so the FE opens the trace inline.
    assert passed["run_id"] is not None
    assert passed["trace_id"] is not None
    assert c.get(f"/api/workspaces/{ws_id}/traces/{passed['trace_id']}").status_code == 200

    failed = by_name["say zzz"]
    assert failed["matched"] is False
    assert failed["expected"]["must_include"] == ["zzz"]
    # The agent produced "pong" (the detected output), which misses "zzz".
    assert failed["detected"]["content"] == "pong"
    assert "missing_required_substring" in failed["failure_modes"]
    assert failed["label"] == "fail"


def test_results_404_for_unknown_experiment(seeded: tuple[TestClient, str, str]) -> None:
    c, ws_id, _exp_id = seeded
    res = c.get(f"/api/workspaces/{ws_id}/experiments/exp_DOESNOTEXIST/results")
    assert res.status_code == 404
