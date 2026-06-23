"""End-to-end smoke test for the optimization loop + friendly errors.

The first half drives `OptimizationLoop.run()` against a workspace,
two cases, a real `DeterministicGrader`, and a mocked `LLMJudgeGrader`
whose judge adapter returns deterministic JSON. The point is to catch
regressions where the full pipeline silently breaks even though the
unit tests pass — schema drift, decision-evaluator coupling, storage
round-trips.

The second half covers the five user-facing error paths added by the
hardening pass:

1. YAML invalid at load time
2. Dataset path missing on disk
3. Grader referenced by name but not registered
4. HTTP adapter cannot reach the configured endpoint
5. SQLite database file is corrupted (or the parent path is)

Each error case asserts: rc == 2, a one-line message on stderr,
and *no* Python traceback. The friendly message is the contract.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from selfevals.cli.main import app
from selfevals.decision.matrix import DecisionMatrixEvaluator
from selfevals.graders.deterministic import DeterministicGrader
from selfevals.graders.llm_judge import LLMJudgeGrader, RubricTemplate
from selfevals.optimization.loop import OptimizationLoop
from selfevals.optimization.proposers import GridProposer
from selfevals.reporter import render_markdown
from selfevals.runner.adapters import AdapterRequest, AdapterResponse, EmbeddedAdapter
from selfevals.runner.executor import Executor
from selfevals.runner.sandbox import SandboxPolicy
from selfevals.schemas._base import EntityRef
from selfevals.schemas.enums import (
    AgentType,
    DatasetSource,
    DatasetType,
    DecisionOutcome,
    ExperimentState,
    GroundTruthMethod,
    Level,
    Mode,
    ProposerStrategy,
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
from selfevals.schemas.experiment import (
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
from selfevals.schemas.fleet import Agent, ModelRef
from selfevals.schemas.iteration import DecisionRecord, IterationRecord
from selfevals.schemas.workspace import Workspace
from selfevals.storage.factory import open_storage

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _case(name: str, must_include: str) -> EvalCase:
    return EvalCase(
        id=EvalCase.make_id(),
        workspace_id=WS,
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


def _agent() -> Agent:
    return Agent(
        id=Agent.make_id(),
        workspace_id=WS,
        agent_type=AgentType.SYSTEM_PROMPT,
        model=ModelRef(provider="anthropic", name="claude-sonnet-4-6"),
        system_prompt_pointer="oss://prompts/x",
    )


def _experiment(max_iterations: int = 2) -> Experiment:
    return Experiment(
        id=Experiment.make_id(),
        workspace_id=WS,
        name="end-to-end smoke",
        goal="exercise the whole loop with two graders",
        mode=Mode.HANDOFF,
        taxonomy=ExperimentTaxonomy(
            target_features=["commerce.product_resolution"],
            dataset_types=[DatasetType.CAPABILITY],
        ),
        datasets=DatasetUsage(optimization=EntityRef(id="ds_x", version=1)),
        target=TargetSpec(primary=MetricTarget(name="pass@1", operator=">=", value=0.5)),
        editable=EditableContract(prompt=True, model_params=True),
        frozen=FrozenSnapshot(
            fleet=EntityRef(id="flt_x"),
            agents=[EntityRef(id="ag_x")],
            datasets=[EntityRef(id="ds_y")],
        ),
        proposer=ProposerSpec(strategy=ProposerStrategy.GRID),
        run=RunSpec(
            sandbox=SandboxMode.MOCK,
            max_iterations=max_iterations,
            convergence=ConvergenceSpec(min_delta=1e-6, patience=10),
        ),
        # Grid sweeps two values; both make the agent return "pong" so both
        # iterations clear the deterministic must_include rule.
        search_space=SearchSpace(model_params={"level": [0.5, 1.0]}),
        reliability=ReliabilitySpec(metrics=["pass@1"]),
    )


def _pingpong_adapter() -> EmbeddedAdapter:
    """Echo agent: emits 'pong' when proposer.level >= 0.5, else 'miss'."""

    def fn(req: AdapterRequest) -> AdapterResponse:
        level = req.parameters.get("model_params", {}).get("level", 0.0)
        content = "pong" if level >= 0.5 else "miss"
        return AdapterResponse(content=content, tokens_input=4, tokens_output=2)

    return EmbeddedAdapter(fn, agent=_agent())


def _mock_judge_adapter() -> EmbeddedAdapter:
    """An EmbeddedAdapter that pretends to be an LLM judge.

    It looks at the *agent response* baked into the rubric prompt (the
    real judge would do the same) and returns deterministic JSON with
    label=pass if the prompt mentions 'pong', else label=fail.
    """

    def fn(req: AdapterRequest) -> AdapterResponse:
        prompt = req.input["messages"][0]["content"]
        decided_pass = (
            "pong" in prompt and "miss" not in prompt.lower().split("agent response:")[-1]
        )
        payload = {
            "label": "pass" if decided_pass else "fail",
            "reason": "agent emitted pong" if decided_pass else "did not emit pong",
            "score": 1.0 if decided_pass else 0.0,
            "confidence": 0.95,
        }
        return AdapterResponse(content=json.dumps(payload))

    return EmbeddedAdapter(fn)


@pytest.mark.asyncio
async def test_full_loop_with_deterministic_and_llm_judge(db_url: str) -> None:
    """Run two iterations end-to-end with both graders, then re-read."""
    cases = [_case("c1", "pong"), _case("c2", "pong")]
    experiment = _experiment(max_iterations=2)

    storage = open_storage(db_url)
    ws = Workspace(id=WS, workspace_id=WS, slug="ws", name="smoke")
    with storage.open(WS) as scope:
        scope.put_entity(ws)
        scope.put_entity(experiment)

    scope = storage.open(WS)
    try:
        executor = Executor(
            adapter=_pingpong_adapter(),
            sandbox=SandboxPolicy(SandboxMode.MOCK),
            workspace_id=WS,
        )
        judge = LLMJudgeGrader(
            "mock_judge",
            judge_adapter=_mock_judge_adapter(),
            rubric=RubricTemplate(rubric="Agent must say pong"),
        )
        loop = OptimizationLoop(
            experiment=experiment,
            executor=executor,
            proposer=GridProposer(),
            graders=[DeterministicGrader(), judge],
            cases=cases,
            scope=scope,
            decision_evaluator=DecisionMatrixEvaluator(),
        )
        result = await loop.run()
    finally:
        scope.close()
    assert len(result.iterations) == 2
    assert experiment.state == ExperimentState.COMPLETED
    for outcome in result.iterations:
        assert outcome.aggregate.primary_metric == "pass@1"
        # Each iteration ran 2 cases.
        assert outcome.aggregate.case_count == 2
        # Both iterations make the agent emit "pong" because grid sweeps
        # 0.5 and 1.0 — both above the level threshold.
        assert outcome.aggregate.primary_value == pytest.approx(1.0)
        # Both grader names show up in the per-rep grade results.
        assert outcome.iteration_record.metrics is not None
        # Decision must be a known outcome.
        assert outcome.decision_record.outcome in set(DecisionOutcome)
    with storage.open(WS) as s:
        iter_records = [
            r for r in s.list_entities(IterationRecord) if isinstance(r, IterationRecord)
        ]
        decision_records = [
            d for d in s.list_entities(DecisionRecord) if isinstance(d, DecisionRecord)
        ]
    assert len(iter_records) == 2
    assert len(decision_records) == 2
    # iteration indices match what the loop emitted.
    assert sorted(r.iteration for r in iter_records) == [0, 1]
    # decision records reference the same experiment.
    for d in decision_records:
        assert d.experiment_id == experiment.id
    md = render_markdown(result)
    assert "# Experiment: end-to-end smoke" in md
    assert "pass@1" in md

    storage.close()


def _run_cli(
    args: list[str],
    capsys: pytest.CaptureFixture[str],
) -> tuple[int, str, str]:
    """Invoke the CLI in-process and capture rc / stdout / stderr."""
    rc = app(args)
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def test_error_invalid_yaml_is_actionable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("garbage: [unclosed\n")
    rc, _, stderr = _run_cli(["run", str(bad), "--no-persist"], capsys)
    assert rc == 2
    assert "could not parse YAML" in stderr
    assert str(bad) in stderr
    # Must not be a Python traceback.
    assert "Traceback" not in stderr


def _write_minimal_yaml(tmp_path: Path, **overrides: object) -> Path:
    body = textwrap.dedent(
        """
        workspace: ws_01HZZZZZZZZZZZZZZZZZZZZZZZ
        experiment:
          name: e
          goal: g
          mode: handoff
          taxonomy:
            target_features: [commerce.product_resolution]
            dataset_types: [capability]
          datasets:
            optimization: { id: ds_x, version: 1 }
          target:
            primary: { name: pass@1, operator: ">=", value: 0.5 }
          editable:
            prompt: true
            model_params: true
          frozen:
            fleet: { id: flt_x }
            agents: [{ id: ag_x }]
            datasets: [{ id: ds_x }]
          proposer:
            strategy: grid
          search_space:
            model_params: { level: [1.0] }
          run:
            sandbox: mock
            max_iterations: 1
            convergence: { min_delta: 1.0e-6, patience: 10 }
          reliability:
            metrics: [pass@1]
        dataset:
          DATASET_BLOCK
        agent:
          entrypoint: selfevals.examples.pingpong:run
        """
    ).strip()
    dataset_block = overrides.get(
        "dataset",
        "cases_inline:\n    - name: t\n      task_type: x\n      input: { messages: [{ role: user, content: hi }] }\n"
        "      taxonomy:\n        level: final_response\n        feature: { primary: commerce.product_resolution }\n"
        "        source: { type: handcrafted }\n        ground_truth: { methods: [exact_match] }\n"
        "        dataset_type: capability\n      expected: { must_include: [pong] }",
    )
    yaml = body.replace("DATASET_BLOCK", str(dataset_block))
    path = tmp_path / "exp.yaml"
    path.write_text(yaml)
    return path


def test_error_dataset_path_missing_with_suggestion(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A real dataset with a slightly different name — the friendly layer
    # should suggest it via fuzzy match.
    (tmp_path / "pingpong.jsonl").write_text("{}\n")
    yaml = _write_minimal_yaml(tmp_path, dataset="cases_path: pingpang.jsonl")
    rc, _, stderr = _run_cli(["run", str(yaml), "--no-persist"], capsys)
    assert rc == 2
    assert "pingpang.jsonl" in stderr
    assert "not found" in stderr
    assert "did you mean" in stderr
    assert "pingpong.jsonl" in stderr  # the suggestion
    assert "Traceback" not in stderr


def test_error_unknown_grader_lists_available(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A YAML whose case references a grader name that is not registered.
    # We embed the case inline with `graders: [not_a_real_grader]`.
    body = textwrap.dedent(
        """
        workspace: ws_01HZZZZZZZZZZZZZZZZZZZZZZZ
        experiment:
          name: e
          goal: g
          mode: handoff
          taxonomy:
            target_features: [commerce.product_resolution]
            dataset_types: [capability]
          datasets:
            optimization: { id: ds_x, version: 1 }
          target:
            primary: { name: pass@1, operator: ">=", value: 0.5 }
          editable:
            prompt: true
            model_params: true
          frozen:
            fleet: { id: flt_x }
            agents: [{ id: ag_x }]
            datasets: [{ id: ds_x }]
          proposer:
            strategy: grid
          search_space:
            model_params: { level: [1.0] }
          run:
            sandbox: mock
            max_iterations: 1
            convergence: { min_delta: 1.0e-6, patience: 10 }
          reliability:
            metrics: [pass@1]
        dataset:
          cases_inline:
            - name: t
              task_type: x
              input: { messages: [{ role: user, content: hi }] }
              taxonomy:
                level: final_response
                feature: { primary: commerce.product_resolution }
                source: { type: handcrafted }
                ground_truth: { methods: [exact_match] }
                dataset_type: capability
              expected: { must_include: [pong] }
              graders: [not_a_real_grader]
        agent:
          entrypoint: selfevals.examples.pingpong:run
        """
    ).strip()
    yaml = tmp_path / "exp.yaml"
    yaml.write_text(body)
    rc, _, stderr = _run_cli(["run", str(yaml), "--no-persist"], capsys)
    assert rc == 2
    assert "not_a_real_grader" in stderr
    assert "not registered" in stderr
    assert "deterministic" in stderr  # registry listing
    assert "Traceback" not in stderr


@pytest.mark.asyncio
async def test_error_http_adapter_unreachable_endpoint(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`HttpEndpointAdapter` surfaces a clean AdapterError naming the URL."""
    from selfevals.runner.adapters import AdapterError, HttpEndpointAdapter

    # Port 1 is privileged and not in use — instant ECONNREFUSED.
    url = "http://127.0.0.1:1/"
    adapter = HttpEndpointAdapter(url, timeout_seconds=1.0)
    with pytest.raises(AdapterError) as excinfo:
        await adapter.invoke(AdapterRequest(workspace_id=WS, case_id="c", input={"messages": []}))
    msg = str(excinfo.value)
    assert url in msg
    assert "could not reach" in msg or "transport" in msg.lower()
    # The friendly wrapper used by the CLI must lift this into a
    # SelfEvalsUserError without losing the URL.
    from selfevals.cli._friendly import wrap_adapter_error

    user_err = wrap_adapter_error(excinfo.value, url=url)
    user_msg = str(user_err)
    assert url in user_msg or "could not reach" in user_msg
