from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from selfeval.schemas._base import EntityRef
from selfeval.schemas.enums import (
    DatasetType,
    DecisionOutcome,
    IterationState,
    Mode,
    ProposerStrategy,
    SandboxMode,
)
from selfeval.schemas.experiment import (
    DatasetUsage,
    EditableContract,
    Experiment,
    ExperimentTaxonomy,
    FrozenSnapshot,
    MetricTarget,
    ProposerSpec,
    RunSpec,
    TargetSpec,
)
from selfeval.schemas.iteration import (
    CodeDiff,
    DecisionRationale,
    DecisionRecord,
    ExecutionInfo,
    HumanRationale,
    IterationDecision,
    IterationMetrics,
    IterationRecord,
    MetricObservation,
    Proposal,
    ProposalRejectedError,
    ProposerInputs,
)

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _experiment(
    *, mode: Mode = Mode.HANDOFF, editable: EditableContract | None = None
) -> Experiment:
    return Experiment(
        id=Experiment.make_id(),
        workspace_id=WS,
        name="x",
        goal="x",
        mode=mode,
        taxonomy=ExperimentTaxonomy(
            target_features=["commerce.product_resolution"],
            dataset_types=[DatasetType.CAPABILITY],
        ),
        datasets=DatasetUsage(optimization=EntityRef(id="ds_x", version=1)),
        target=TargetSpec(primary=MetricTarget(name="pass@1", operator=">=", value=0.85)),
        editable=editable or EditableContract(),
        frozen=FrozenSnapshot(
            fleet=EntityRef(id="flt_x"),
            agents=[EntityRef(id="ag_x")],
            datasets=[EntityRef(id="ds_y")],
        ),
        proposer=ProposerSpec(strategy=ProposerStrategy.GRID),
        run=RunSpec(sandbox=SandboxMode.DRY_RUN),
    )


def test_proposal_clean_passes() -> None:
    exp = _experiment()
    p = Proposal(parameters={"prompt": "new prompt"}, hypothesis="prompt improvement")
    p.validate_against(exp)


def test_proposal_touches_non_editable_field_rejected() -> None:
    exp = _experiment(editable=EditableContract(prompt=True, tool_code=False))
    p = Proposal(parameters={"tool_code": "new"}, hypothesis="hack tool")
    with pytest.raises(ProposalRejectedError) as exc:
        p.validate_against(exp)
    assert "tool_code" in exc.value.violations


def test_proposal_code_changes_in_handoff_rejected() -> None:
    exp = _experiment()  # handoff, no editable.tool_code
    p = Proposal(
        parameters={},
        hypothesis="refactor",
        code_changes=[CodeDiff(path="src/x.py", operation="modify")],
    )
    with pytest.raises(ProposalRejectedError) as exc:
        p.validate_against(exp)
    assert "code_changes" in exc.value.violations


def test_proposal_code_changes_in_agent_loop_with_tool_code_ok() -> None:
    exp = _experiment(mode=Mode.AGENT_LOOP, editable=EditableContract(tool_code=True))
    p = Proposal(
        parameters={"tool_code": "improved"},
        hypothesis="improve tool body",
        code_changes=[CodeDiff(path="tools/search.py", operation="modify")],
    )
    p.validate_against(exp)


def test_proposal_search_space_expansion_rejected_by_default() -> None:
    exp = _experiment()
    p = Proposal(
        parameters={"prompt": "x"},
        hypothesis="expand",
        search_space_expansion_request={"temperature": [0.0, 2.0]},
    )
    with pytest.raises(ProposalRejectedError) as exc:
        p.validate_against(exp)
    assert "search_space_expansion_request" in exc.value.violations


def test_proposal_opaque_keys_ignored_by_contract() -> None:
    exp = _experiment(editable=EditableContract(prompt=False, model_params=True))
    # 'temperature' is not in _PROPOSAL_KEY_TO_EDITABLE → treated as opaque
    p = Proposal(parameters={"_meta_iteration_seed": 42}, hypothesis="x")
    p.validate_against(exp)


def test_proposal_violations_against_returns_empty_when_clean() -> None:
    exp = _experiment()
    p = Proposal(parameters={"prompt": "x"}, hypothesis="x")
    assert p.violations_against(exp.editable) == []


def _execution(variant: str = "v1") -> ExecutionInfo:
    return ExecutionInfo(variant_id=variant, trace_run_ids=[])


def _iteration(**overrides: Any) -> IterationRecord:
    base: dict[str, Any] = {
        "id": IterationRecord.make_id(),
        "workspace_id": WS,
        "experiment_id": "exp_x",
        "iteration": 1,
        "state": IterationState.COMPLETED,
        "proposer": ProposerInputs(type=ProposerStrategy.GRID),
        "hypothesis": "raise temperature improves recall",
        "execution": _execution(),
        "metrics": IterationMetrics(
            primary=MetricObservation(name="pass@1", value=0.87, delta_vs_baseline=0.02),
        ),
        "decision": IterationDecision(
            outcome=DecisionOutcome.KEEP_CANDIDATE,
            rationale="primary above target, guardrails pass",
        ),
    }
    base.update(overrides)
    return IterationRecord(**base)


def test_iteration_happy() -> None:
    itr = _iteration()
    assert itr.state == IterationState.COMPLETED


def test_failure_mode_counts_survive_serialization_round_trip() -> None:
    # Storage persists the JSON payload, so a model_dump/model_validate
    # round-trip is exactly the persistence path. error_analysis_design.md §5.
    itr = _iteration(
        metrics=IterationMetrics(
            primary=MetricObservation(name="pass@1", value=0.8),
            failure_mode_counts={"fm_invented_price": 3, "schema_violation": 1},
        )
    )
    restored = IterationRecord.model_validate(itr.model_dump(mode="json"))
    assert restored.metrics is not None
    assert restored.metrics.failure_mode_counts == {
        "fm_invented_price": 3,
        "schema_violation": 1,
    }


def test_failure_mode_counts_default_empty() -> None:
    itr = _iteration()
    assert itr.metrics is not None
    assert itr.metrics.failure_mode_counts == {}


def test_iteration_completed_requires_metrics() -> None:
    with pytest.raises(ValidationError):
        _iteration(metrics=None)


def test_iteration_completed_requires_decision() -> None:
    with pytest.raises(ValidationError):
        _iteration(decision=None)


def test_iteration_failed_allows_no_metrics() -> None:
    itr = _iteration(state=IterationState.FAILED, metrics=None, decision=None)
    assert itr.metrics is None


def test_iteration_parent_must_be_strictly_earlier() -> None:
    with pytest.raises(ValidationError):
        _iteration(iteration=2, parent_iteration=2)
    with pytest.raises(ValidationError):
        _iteration(iteration=2, parent_iteration=3)


def test_iteration_parent_earlier_ok() -> None:
    itr = _iteration(iteration=3, parent_iteration=1)
    assert itr.parent_iteration == 1


def test_decision_record_minimal() -> None:
    dec = DecisionRecord(
        id=DecisionRecord.make_id(),
        workspace_id=WS,
        experiment_id="exp_x",
        iteration=2,
        variant_id="v2",
        outcome=DecisionOutcome.KEEP_CANDIDATE,
        rationale=DecisionRationale(automated="primary up 0.04, guardrails ok"),
    )
    assert dec.outcome == DecisionOutcome.KEEP_CANDIDATE
    assert dec.rationale.human is None


def test_decision_record_with_human_override() -> None:
    dec = DecisionRecord(
        id=DecisionRecord.make_id(),
        workspace_id=WS,
        experiment_id="exp_x",
        iteration=2,
        variant_id="v2",
        outcome=DecisionOutcome.REJECT,
        rationale=DecisionRationale(
            automated="primary up 0.04",
            human=HumanRationale(
                decided_by="patricio",
                decided_at=datetime(2026, 5, 16, tzinfo=UTC),
                notes="judge looked suspicious",
                overrides_automated=True,
            ),
        ),
    )
    assert dec.rationale.human is not None
    assert dec.rationale.human.overrides_automated is True
