from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from bootstrap.schemas._base import EntityRef
from bootstrap.schemas.enums import (
    DatasetType,
    ExperimentState,
    Mode,
    ProposerStrategy,
    SandboxMode,
)
from bootstrap.schemas.experiment import (
    DatasetUsage,
    EditableContract,
    Experiment,
    ExperimentTaxonomy,
    FrozenSnapshot,
    IllegalStateTransitionError,
    JudgeDefenses,
    MetricTarget,
    OutcomeMetricsSpec,
    ProposerSpec,
    ReliabilitySpec,
    RunSpec,
    TargetSpec,
)

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _frozen() -> FrozenSnapshot:
    return FrozenSnapshot(
        fleet=EntityRef(id="flt_01HZZZZZZZZZZZZZZZZZZZZZZZ", version=1),
        agents=[EntityRef(id="ag_01HZZZZZZZZZZZZZZZZZZZZZZZ", version=1)],
        datasets=[EntityRef(id="ds_01HZZZZZZZZZZZZZZZZZZZZZZZ", version=1)],
    )


def _experiment(**overrides: Any) -> Experiment:
    base: dict[str, Any] = {
        "id": Experiment.make_id(),
        "workspace_id": WS,
        "name": "raise pass@1 on product_resolution",
        "goal": "Raise pass@1 on product resolution capability set above 0.85.",
        "mode": Mode.HANDOFF,
        "taxonomy": ExperimentTaxonomy(
            target_features=["commerce.product_resolution"],
            dataset_types=[DatasetType.CAPABILITY],
        ),
        "datasets": DatasetUsage(
            optimization=EntityRef(id="ds_opt", version=1),
            gates=[EntityRef(id="ds_reg", version=1)],
        ),
        "target": TargetSpec(
            primary=MetricTarget(name="pass@1", operator=">=", value=0.85),
            guardrails=[MetricTarget(name="cost_usd_per_case", operator="<=", value=0.02)],
        ),
        "frozen": _frozen(),
        "proposer": ProposerSpec(strategy=ProposerStrategy.GRID),
        "run": RunSpec(sandbox=SandboxMode.DRY_RUN),
    }
    base.update(overrides)
    return Experiment(**base)


def test_experiment_happy() -> None:
    exp = _experiment()
    assert exp.state == ExperimentState.DRAFT
    assert exp.mode == Mode.HANDOFF
    assert exp.proposer.allow_search_space_expansion is False


def test_editable_tool_code_in_handoff_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        _experiment(editable=EditableContract(tool_code=True))
    assert "agent_loop" in str(exc.value)


def test_editable_tool_code_with_agent_loop_ok() -> None:
    exp = _experiment(mode=Mode.AGENT_LOOP, editable=EditableContract(tool_code=True))
    assert exp.editable.tool_code is True


def test_editable_workflow_graph_requires_agent_loop() -> None:
    with pytest.raises(ValidationError):
        _experiment(editable=EditableContract(workflow_graph=True))


def test_editable_skills_requires_agent_loop() -> None:
    with pytest.raises(ValidationError):
        _experiment(editable=EditableContract(skills=True))


def test_canary_requires_outcome_metrics() -> None:
    with pytest.raises(ValidationError) as exc:
        _experiment(run=RunSpec(sandbox=SandboxMode.LIVE_CANARY))
    assert "outcome_metrics" in str(exc.value)


def test_canary_with_outcome_metrics_ok() -> None:
    exp = _experiment(
        run=RunSpec(sandbox=SandboxMode.LIVE_CANARY),
        judge_defenses=JudgeDefenses(
            outcome_metrics=OutcomeMetricsSpec(
                metrics=["human_approval_rate", "escalation_rate"]
            )
        ),
    )
    assert exp.run.sandbox == SandboxMode.LIVE_CANARY


def test_post_mvp_proposer_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        _experiment(proposer=ProposerSpec(strategy=ProposerStrategy.BAYESIAN))
    assert "post-MVP" in str(exc.value)


@pytest.mark.parametrize(
    "metric",
    ["pass@1", "pass@5", "pass^3", "pass^10", "consistency_rate", "stability_score", "recovery_rate"],
)
def test_reliability_metric_names_valid(metric: str) -> None:
    spec = ReliabilitySpec(metrics=[metric])
    assert spec.metrics == [metric]


@pytest.mark.parametrize("metric", ["pass", "pass@", "pass^abc", "accuracy_avg", ""])
def test_reliability_metric_names_invalid(metric: str) -> None:
    with pytest.raises(ValidationError):
        ReliabilitySpec(metrics=[metric])


# --- state machine ---


def test_state_machine_happy_path() -> None:
    exp = _experiment()
    exp.transition_to(ExperimentState.QUEUED)
    exp.transition_to(ExperimentState.RUNNING)
    exp.transition_to(ExperimentState.COMPLETED)
    exp.transition_to(ExperimentState.SUPERSEDED)
    assert exp.is_terminal()


def test_state_machine_pause_and_resume() -> None:
    exp = _experiment()
    exp.transition_to(ExperimentState.QUEUED)
    exp.transition_to(ExperimentState.RUNNING)
    exp.transition_to(ExperimentState.PAUSED)
    exp.transition_to(ExperimentState.RUNNING)


def test_state_machine_illegal_jump() -> None:
    exp = _experiment()
    with pytest.raises(IllegalStateTransitionError):
        exp.transition_to(ExperimentState.COMPLETED)


def test_state_machine_aborted_is_dead_end() -> None:
    exp = _experiment()
    exp.transition_to(ExperimentState.ABORTED)
    with pytest.raises(IllegalStateTransitionError):
        exp.transition_to(ExperimentState.RUNNING)


def test_state_machine_completed_only_to_superseded() -> None:
    exp = _experiment()
    exp.transition_to(ExperimentState.QUEUED)
    exp.transition_to(ExperimentState.RUNNING)
    exp.transition_to(ExperimentState.COMPLETED)
    with pytest.raises(IllegalStateTransitionError):
        exp.transition_to(ExperimentState.RUNNING)
    exp.transition_to(ExperimentState.SUPERSEDED)


def test_dataset_types_must_be_unique() -> None:
    with pytest.raises(ValidationError):
        ExperimentTaxonomy(
            target_features=["x"],
            dataset_types=[DatasetType.CAPABILITY, DatasetType.CAPABILITY],
        )


def test_frozen_requires_at_least_one_agent_and_dataset() -> None:
    with pytest.raises(ValidationError):
        FrozenSnapshot(
            fleet=EntityRef(id="flt_x"),
            agents=[],
            datasets=[EntityRef(id="ds_x")],
        )
    with pytest.raises(ValidationError):
        FrozenSnapshot(
            fleet=EntityRef(id="flt_x"),
            agents=[EntityRef(id="ag_x")],
            datasets=[],
        )
