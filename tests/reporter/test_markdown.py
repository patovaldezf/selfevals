from __future__ import annotations

import asyncio
import json

from selfevals.decision.matrix import DecisionMatrixEvaluator
from selfevals.graders.deterministic import DeterministicGrader
from selfevals.optimization.aggregator import (
    CaseOutcome,
    IterationAggregate,
)
from selfevals.optimization.loop import (
    IterationOutcome,
    OptimizationLoop,
    OptimizationResult,
)
from selfevals.optimization.proposers import GridProposer
from selfevals.reporter import render_json, render_markdown
from selfevals.reporter.json_report import to_dict
from selfevals.runner.adapters import AdapterRequest, AdapterResponse, EmbeddedAdapter
from selfevals.runner.executor import Executor
from selfevals.runner.sandbox import SandboxPolicy
from selfevals.schemas._base import EntityRef
from selfevals.schemas.enums import (
    AgentType,
    DatasetSource,
    DatasetType,
    DecisionOutcome,
    GroundTruthMethod,
    IterationState,
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
from selfevals.schemas.iteration import (
    DecisionRationale,
    DecisionRecord,
    ExecutionInfo,
    IterationDecision,
    IterationMetrics,
    IterationRecord,
    MetricObservation,
    Proposal,
    ProposerInputs,
)

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _case(target: str = "pong") -> EvalCase:
    return EvalCase(
        id=EvalCase.make_id(),
        workspace_id=WS,
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


def _experiment(**overrides) -> Experiment:
    base = dict(
        id=Experiment.make_id(),
        workspace_id=WS,
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


def _agent() -> Agent:
    return Agent(
        id=Agent.make_id(),
        workspace_id=WS,
        agent_type=AgentType.SYSTEM_PROMPT,
        model=ModelRef(provider="anthropic", name="claude-sonnet-4-6"),
        system_prompt_pointer="oss://prompts/x",
    )


def _adapter_for(target: str) -> EmbeddedAdapter:
    def fn(req: AdapterRequest) -> AdapterResponse:
        level = req.parameters.get("model_params", {}).get("level", 0.0)
        content = target if level >= 0.5 else "miss"
        return AdapterResponse(content=content, tokens_input=4, tokens_output=2)

    return EmbeddedAdapter(fn, agent=_agent())


def _run_real() -> OptimizationResult:
    exp = _experiment()
    cases = [_case("pong")]
    executor = Executor(
        adapter=_adapter_for("pong"),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    loop = OptimizationLoop(
        experiment=exp,
        executor=executor,
        proposer=GridProposer(),
        graders=[DeterministicGrader()],
        cases=cases,
        decision_evaluator=DecisionMatrixEvaluator(),
    )
    return asyncio.run(loop.run())


def test_render_markdown_includes_header_and_table() -> None:
    result = _run_real()
    md = render_markdown(result)
    # Heading + goal.
    assert md.startswith("# Experiment: optimize prompt v2")
    assert "lift pass@1" in md
    # Target line.
    assert "`pass@1` >= 0.5" in md
    # Table header is present.
    assert "| # | primary | Δ | outcome | rationale |" in md
    # Two iterations from the grid → two table rows.
    assert md.count("\n| 0 |") == 1
    assert md.count("\n| 1 |") == 1


def test_render_markdown_best_iteration_callout() -> None:
    result = _run_real()
    md = render_markdown(result)
    # Best iteration is the one with level=1.0 (pass@1 = 1.0).
    assert "## Best iteration" in md
    assert "pass@1 = 1" in md


def test_render_markdown_handles_empty_result() -> None:
    exp = _experiment()
    result = OptimizationResult(experiment=exp, terminated_reason="search_space_exhausted: x")
    md = render_markdown(result)
    assert "No iterations were executed." in md
    assert "search_space_exhausted" in md


def test_render_markdown_escapes_pipe_in_rationale() -> None:
    exp = _experiment()
    result = OptimizationResult(experiment=exp)
    result.iterations.append(_synthetic_iteration(rationale="a | b | c"))
    md = render_markdown(result)
    # Pipe in rationale must be escaped so the table doesn't break.
    assert "a \\| b \\| c" in md


def test_render_markdown_top_failure_modes_section() -> None:
    exp = _experiment()
    result = OptimizationResult(experiment=exp)
    result.iterations.append(
        _synthetic_iteration(
            primary=0.3,
            failure_modes={"missing_must_include": 4, "extra_forbidden_tool": 1},
        )
    )
    md = render_markdown(result, top_failure_modes=2)
    assert "## Top failure modes" in md
    assert "`missing_must_include` — 4" in md
    assert "`extra_forbidden_tool` — 1" in md


def test_render_markdown_truncates_long_rationale() -> None:
    exp = _experiment()
    result = OptimizationResult(experiment=exp)
    long = "x" * 200
    result.iterations.append(_synthetic_iteration(rationale=long))
    md = render_markdown(result)
    # Truncated to 77 + ellipsis.
    assert "x" * 77 + "…" in md
    assert "x" * 200 not in md


def test_render_json_round_trips() -> None:
    result = _run_real()
    payload = json.loads(render_json(result))
    assert payload["schema_version"] == "1"
    assert payload["experiment"]["name"] == "optimize prompt v2"
    assert payload["experiment"]["primary_metric"] == "pass@1"
    assert payload["termination"]["iterations_run"] == len(result.iterations)
    assert len(payload["iterations"]) == len(result.iterations)
    # Best iteration is referenced by index.
    assert payload["best_iteration"] is not None
    assert payload["best_iteration"]["iteration"] in {0, 1}


def test_render_json_empty_result_has_no_best() -> None:
    exp = _experiment()
    result = OptimizationResult(experiment=exp, terminated_reason="aborted")
    payload = to_dict(result)
    assert payload["best_iteration"] is None
    assert payload["iterations"] == []
    assert payload["termination"]["reason"] == "aborted"


def test_render_json_includes_decision_outcome_string() -> None:
    exp = _experiment()
    result = OptimizationResult(experiment=exp)
    result.iterations.append(_synthetic_iteration(outcome=DecisionOutcome.REJECT))
    payload = to_dict(result)
    assert payload["iterations"][0]["decision"]["outcome"] == "reject"


def test_markdown_omits_cost_time_section_when_no_data() -> None:
    """Echo agents report 0 cost / 0 time. We must not render the
    section in that case — a "$0.00" placeholder would mislead readers
    into thinking cost was actually measured."""
    exp = _experiment()
    result = OptimizationResult(experiment=exp)
    result.iterations.append(_synthetic_iteration(primary=0.5, cost=0.0, duration_ms=0))
    md = render_markdown(result)
    assert "## Cost & Time" not in md
    assert "$0.00" not in md
    assert "0.00s" not in md


def test_markdown_includes_cost_time_section_when_data_present() -> None:
    exp = _experiment()
    result = OptimizationResult(experiment=exp)
    result.iterations.append(_synthetic_iteration(primary=0.5, cost=0.12, duration_ms=2500))
    result.iterations.append(_synthetic_iteration(primary=0.8, cost=0.18, duration_ms=2500))
    md = render_markdown(result)
    assert "## Cost & Time" in md
    assert "Total cost" in md
    assert "Total time" in md
    # Sanity-check that the formatted totals appear.
    assert "0.30" in md
    assert "5.00s" in md


def test_markdown_includes_next_steps_section() -> None:
    """Reader should always see suggested follow-up commands."""
    result = _run_real()
    md = render_markdown(result)
    assert "## Next steps" in md
    assert "selfevals iteration list" in md
    assert "selfevals report" in md
    # With 2+ iterations, a compare command is suggested too.
    assert "selfevals compare" in md


def test_markdown_idempotent_under_repeat_renders() -> None:
    exp = _experiment()
    result = OptimizationResult(experiment=exp)
    result.iterations.append(_synthetic_iteration(primary=0.5, cost=0.12, duration_ms=2500))
    assert render_markdown(result) == render_markdown(result)


def test_json_includes_cost_time_block_always() -> None:
    """JSON shape is stable: the cost_time block is always present,
    with None for unknown fields (machine readers prefer keys)."""
    exp = _experiment()
    result = OptimizationResult(experiment=exp)
    result.iterations.append(_synthetic_iteration(primary=0.5, cost=0.0, duration_ms=0))
    payload = to_dict(result)
    assert "cost_time" in payload
    assert payload["cost_time"]["cost_total_usd"] is None
    assert payload["cost_time"]["time_total_seconds"] is None
    assert payload["cost_time"]["iterations"] == 1


def test_json_cost_time_block_populated_with_data() -> None:
    exp = _experiment()
    result = OptimizationResult(experiment=exp)
    result.iterations.append(_synthetic_iteration(primary=0.5, cost=0.12, duration_ms=2000))
    payload = to_dict(result)
    assert payload["cost_time"]["cost_total_usd"] is not None
    assert payload["cost_time"]["time_total_seconds"] == 2.0


def _synthetic_iteration(
    *,
    primary: float = 0.5,
    rationale: str = "rationale",
    outcome: DecisionOutcome = DecisionOutcome.KEEP_CANDIDATE,
    failure_modes: dict[str, int] | None = None,
    cost: float = 0.0,
    duration_ms: int = 0,
) -> IterationOutcome:
    aggregate = IterationAggregate(
        primary_metric="pass@1",
        primary_value=primary,
        guardrails={},
        reliability={},
        failure_mode_counts=failure_modes or {},
        total_cost_usd=cost,
        total_duration_ms=duration_ms,
        case_count=1,
        case_outcomes=[
            CaseOutcome(
                case_id="ec_x",
                per_repetition_label=[],
                per_repetition_score=[],
            )
        ],
    )
    proposal = Proposal(parameters={"x": 1}, hypothesis="h")
    iteration_record = IterationRecord(
        id=IterationRecord.make_id(),
        workspace_id=WS,
        experiment_id="exp_x",
        iteration=0,
        state=IterationState.COMPLETED,
        proposer=ProposerInputs(type=ProposerStrategy.MANUAL),
        hypothesis="h",
        proposed_parameters={"x": 1},
        execution=ExecutionInfo(variant_id="var_x"),
        metrics=IterationMetrics(
            primary=MetricObservation(name="pass@1", value=primary),
        ),
        decision=IterationDecision(outcome=outcome, rationale=rationale),
    )
    decision_record = DecisionRecord(
        id=DecisionRecord.make_id(),
        workspace_id=WS,
        experiment_id="exp_x",
        iteration=0,
        variant_id="var_x",
        outcome=outcome,
        rationale=DecisionRationale(automated=rationale),
    )
    return IterationOutcome(
        iteration=0,
        proposal=proposal,
        aggregate=aggregate,
        case_runs=[],
        iteration_record=iteration_record,
        decision_record=decision_record,
    )
