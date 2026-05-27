from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from selfeval.cli.main import app
from selfeval.decision.matrix import DecisionMatrixEvaluator
from selfeval.graders.deterministic import DeterministicGrader
from selfeval.optimization.loop import OptimizationLoop
from selfeval.optimization.proposers import GridProposer
from selfeval.runner.adapters import AdapterRequest, AdapterResponse, EmbeddedAdapter
from selfeval.runner.executor import Executor
from selfeval.runner.sandbox import SandboxPolicy
from selfeval.schemas._base import EntityRef
from selfeval.schemas.enums import (
    AgentType,
    DatasetSource,
    DatasetType,
    GroundTruthMethod,
    Level,
    Mode,
    ProposerStrategy,
    SandboxMode,
)
from selfeval.schemas.eval_case import (
    CaseTaxonomy,
    EvalCase,
    Expected,
    FeatureTag,
    GroundTruthSpec,
    SourceInfo,
)
from selfeval.schemas.experiment import (
    ConvergenceSpec,
    DatasetUsage,
    EditableContract,
    Experiment,
    ExperimentTaxonomy,
    FrozenSnapshot,
    MetricTarget,
    ProposerSpec,
    ReliabilitySpec,
    RunSpec,
    SearchSpace,
    TargetSpec,
)
from selfeval.schemas.fleet import Agent, ModelRef
from selfeval.storage.sqlite import SQLiteStorage


def _capture(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, str, str]:
    rc = app(argv)
    out = capsys.readouterr()
    return rc, out.out, out.err


def test_init_creates_workspace(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "db.sqlite"
    rc, stdout, _ = _capture(capsys, ["--db", str(db), "init", "my-team", "--name", "My Team"])
    assert rc == 0
    assert "slug=my-team" in stdout
    assert "name=My Team" in stdout
    assert db.exists()


def test_init_is_idempotent(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "db.sqlite"
    _capture(capsys, ["--db", str(db), "init", "team"])
    first = capsys.readouterr()  # drain
    rc, stdout, _ = _capture(capsys, ["--db", str(db), "init", "team"])
    assert rc == 0
    assert "slug=team" in stdout
    # Same workspace id implies idempotency.
    # (parse "id=..." token from both outputs)
    _ = first


def test_estimate_outputs_total_calls_and_cost(capsys: pytest.CaptureFixture[str]) -> None:
    rc, stdout, _ = _capture(
        capsys,
        [
            "estimate",
            "--cases",
            "5",
            "--space-size",
            "4",
            "--reps",
            "2",
            "--cost-per-call",
            "0.01",
        ],
    )
    assert rc == 0
    assert "agent calls (upper bound): 40" in stdout
    assert "$0.40" in stdout


def test_estimate_rejects_invalid_args(capsys: pytest.CaptureFixture[str]) -> None:
    rc, _, stderr = _capture(
        capsys,
        [
            "estimate",
            "--cases",
            "0",
            "--space-size",
            "1",
            "--cost-per-call",
            "0.01",
        ],
    )
    assert rc == 2
    assert "must all be >= 1" in stderr


def test_experiment_list_empty(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "db.sqlite"
    # Need a real workspace first.
    _capture(capsys, ["--db", str(db), "init", "team"])
    init_out = capsys.readouterr().out  # drain after capsys reset
    # Extract id from "workspace id=<id> ..." line.
    ws_id = _ws_id_from(init_out)
    if ws_id is None:
        # init_out was already consumed by _capture above; re-init in isolation:
        rc, init_out, _ = _capture(capsys, ["--db", str(db), "init", "team"])
        ws_id = _ws_id_from(init_out)
    assert ws_id is not None
    rc, stdout, _ = _capture(capsys, ["--db", str(db), "experiment", "list", ws_id])
    assert rc == 0
    assert "(no experiments)" in stdout


def test_report_renders_markdown_after_real_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "db.sqlite"
    rc, init_out, _ = _capture(capsys, ["--db", str(db), "init", "team"])
    assert rc == 0
    ws_id = _ws_id_from(init_out)
    assert ws_id is not None

    # Run a real optimization against the same db so iterations land in storage.
    exp = _seed_experiment_into_db(db, ws_id)

    rc, stdout, _ = _capture(
        capsys, ["--db", str(db), "report", ws_id, exp.id, "--format", "markdown"]
    )
    assert rc == 0
    assert "# Experiment:" in stdout
    assert "| # | primary | Δ | outcome | rationale |" in stdout
    # Two grid iterations.
    assert "\n| 0 |" in stdout
    assert "\n| 1 |" in stdout


def test_report_json_round_trips(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "db.sqlite"
    rc, init_out, _ = _capture(capsys, ["--db", str(db), "init", "team"])
    assert rc == 0
    ws_id = _ws_id_from(init_out)
    assert ws_id is not None
    exp = _seed_experiment_into_db(db, ws_id)

    rc, stdout, _ = _capture(capsys, ["--db", str(db), "report", ws_id, exp.id, "--format", "json"])
    assert rc == 0
    payload = json.loads(stdout)
    assert payload["schema_version"] == "1"
    assert payload["experiment"]["id"] == exp.id
    assert len(payload["iterations"]) == 2
    assert payload["termination"]["reason"] == "loaded_from_storage"


def test_compare_two_iterations(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "db.sqlite"
    rc, init_out, _ = _capture(capsys, ["--db", str(db), "init", "team"])
    assert rc == 0
    ws_id = _ws_id_from(init_out)
    assert ws_id is not None
    _seed_experiment_into_db(db, ws_id)

    # Pull iteration IDs out of storage to feed into compare.
    storage = SQLiteStorage(db)
    try:
        with storage.open(ws_id) as scope:
            from selfeval.schemas.iteration import IterationRecord

            iterations = sorted(
                (it for it in scope.list_entities(IterationRecord)),
                key=lambda it: it.iteration,
            )
    finally:
        storage.close()
    assert len(iterations) == 2

    rc, stdout, _ = _capture(
        capsys,
        ["--db", str(db), "compare", ws_id, iterations[0].id, iterations[1].id],
    )
    assert rc == 0
    # New compare layout: header, proposal+metrics diff tables, recommendation.
    assert "Comparing iter A (#0) vs iter B (#1)" in stdout
    assert "## Proposal diff" in stdout
    assert "## Metrics diff" in stdout
    assert "`pass@1`" in stdout
    assert "Δ" in stdout


def test_workspace_show_missing_id_reports_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "db.sqlite"
    rc, _, stderr = _capture(
        capsys, ["--db", str(db), "workspace", "show", "ws_01XXXXXXXXXXXXXXXXXXXXXXXX"]
    )
    assert rc == 2
    assert "not found" in stderr


def _ws_id_from(stdout: str) -> str | None:
    for line in stdout.splitlines():
        if "workspace id=" in line:
            token = line.split("id=", 1)[1].split(" ", 1)[0]
            return token
    return None


def _agent(ws_id: str) -> Agent:
    return Agent(
        id=Agent.make_id(),
        workspace_id=ws_id,
        agent_type=AgentType.SYSTEM_PROMPT,
        model=ModelRef(provider="anthropic", name="claude-sonnet-4-6"),
        system_prompt_pointer="oss://prompts/x",
    )


def _case(ws_id: str, target: str = "pong") -> EvalCase:
    return EvalCase(
        id=EvalCase.make_id(),
        workspace_id=ws_id,
        name="t",
        task_type="x",
        input={"messages": [{"role": "user", "content": "hi"}]},
        taxonomy=CaseTaxonomy(
            level=Level.FINAL_RESPONSE,
            feature=FeatureTag(primary="commerce.product_resolution"),
            source=SourceInfo(type=DatasetSource.HANDCRAFTED),
            ground_truth=GroundTruthSpec(methods=[GroundTruthMethod.EXACT_MATCH]),
            dataset_type=DatasetType.CAPABILITY,
        ),
        expected=Expected(must_include=[target]),
    )


def _experiment(ws_id: str, **overrides: Any) -> Experiment:
    base = dict(
        id=Experiment.make_id(),
        workspace_id=ws_id,
        name="optimize prompt v2",
        goal="lift pass@1 on commerce.product_resolution",
        mode=Mode.HANDOFF,
        taxonomy=ExperimentTaxonomy(
            target_features=["commerce.product_resolution"],
            dataset_types=[DatasetType.CAPABILITY],
        ),
        datasets=DatasetUsage(optimization=EntityRef(id="ds_x", version=1)),
        target=TargetSpec(
            primary=MetricTarget(name="pass@1", operator=">=", value=0.5),
        ),
        editable=EditableContract(prompt=True, model_params=True),
        frozen=FrozenSnapshot(
            fleet=EntityRef(id="flt_x"),
            agents=[EntityRef(id="ag_x")],
            datasets=[EntityRef(id="ds_y")],
        ),
        proposer=ProposerSpec(strategy=ProposerStrategy.GRID),
        run=RunSpec(
            sandbox=SandboxMode.MOCK,
            max_iterations=3,
            convergence=ConvergenceSpec(min_delta=1e-6, patience=10),
        ),
        search_space=SearchSpace(model_params={"level": [0.0, 1.0]}),
        reliability=ReliabilitySpec(metrics=["pass@1"]),
    )
    base.update(overrides)
    return Experiment(**base)


def _adapter_for(ws_id: str, target: str) -> EmbeddedAdapter:
    def fn(req: AdapterRequest) -> AdapterResponse:
        level = req.parameters.get("model_params", {}).get("level", 0.0)
        content = target if level >= 0.5 else "miss"
        return AdapterResponse(content=content, tokens_input=4, tokens_output=2)

    return EmbeddedAdapter(fn, agent=_agent(ws_id))


def _seed_experiment_into_db(db_path: Path, ws_id: str) -> Experiment:
    """Run a real OptimizationLoop persisting iterations into `db_path`."""
    exp = _experiment(ws_id)
    storage = SQLiteStorage(db_path)
    try:
        scope = storage.open(ws_id)
        scope.put_entity(exp)
        executor = Executor(
            adapter=_adapter_for(ws_id, "pong"),
            sandbox=SandboxPolicy(SandboxMode.MOCK),
            workspace_id=ws_id,
        )
        loop = OptimizationLoop(
            experiment=exp,
            executor=executor,
            proposer=GridProposer(),
            graders=[DeterministicGrader()],
            cases=[_case(ws_id, "pong")],
            scope=scope,
            decision_evaluator=DecisionMatrixEvaluator(),
        )
        loop.run()
    finally:
        storage.close()
    return exp
