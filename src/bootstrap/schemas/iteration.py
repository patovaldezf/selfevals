"""IterationRecord, Proposal, DecisionRecord — per-iteration ledger.

A Proposal is what a Proposer emits before running an iteration. It is
validated against the parent Experiment's `editable` contract: any field
it tries to mutate that is not editable causes rejection (operational
§A.7.4).

An IterationRecord is the persisted record of one attempt: hypothesis,
proposer inputs, metrics, decision.

A DecisionRecord is the audit trail for what happened to a candidate:
keep / reject / revert / feature_flag / investigate / require_tradeoff_review
/ spawn_subexperiment, with both automated and (optional) human rationale.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

from pydantic import Field, model_validator

from bootstrap.schemas._base import BaseEntity, BootstrapModel, NonEmptyStr
from bootstrap.schemas.enums import DecisionOutcome, IterationState, ProposerStrategy
from bootstrap.schemas.experiment import EditableContract, Experiment


class ProposalRejectedError(ValueError):
    """Raised by Proposal.validate_against when a proposal violates the
    editable contract of its parent experiment."""

    def __init__(self, violations: list[str]) -> None:
        self.violations = violations
        super().__init__(
            "proposal violates editable contract; "
            f"non-editable fields touched: {sorted(violations)}"
        )


# Map from the top-level keys a Proposal can set to the EditableContract
# flag that gates them. Keys not in this map are treated as opaque metadata
# and always allowed (e.g. proposer-internal annotations).
_PROPOSAL_KEY_TO_EDITABLE: dict[str, str] = {
    "prompt": "prompt",
    "system_prompt": "prompt",
    "model": "model_choice",
    "model_params": "model_params",
    "tool_descriptions": "tool_descriptions",
    "tool_code": "tool_code",
    "workflow_graph": "workflow_graph",
    "graph": "workflow_graph",
    "skills": "skills",
    "dataset": "dataset",
    "graders": "graders",
}


class CodeDiff(BootstrapModel):
    """A code change emitted only by agent_loop proposals."""

    path: NonEmptyStr
    operation: NonEmptyStr  # "create" | "modify" | "delete" — left free for MVP
    diff_pointer: str | None = None
    diff_hash: str | None = None


class Proposal(BootstrapModel):
    """Output of a Proposer for one iteration."""

    parameters: dict[str, Any] = Field(default_factory=dict)
    hypothesis: NonEmptyStr
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    code_changes: list[CodeDiff] = Field(default_factory=list)
    """Only legal in agent_loop experiments with the corresponding editable
    flag (tool_code/workflow_graph/skills). Enforced in validate_against."""

    inputs_referenced: list[NonEmptyStr] = Field(default_factory=list)
    search_space_expansion_request: dict[str, Any] | None = None

    def violations_against(self, editable: EditableContract) -> list[str]:
        """Return the list of editable-contract violations, or [] if clean."""
        bad: list[str] = []
        for key in self.parameters:
            gating_flag = _PROPOSAL_KEY_TO_EDITABLE.get(key)
            if gating_flag is None:
                continue
            if not getattr(editable, gating_flag):
                bad.append(key)
        if self.code_changes and not (
            editable.tool_code or editable.workflow_graph or editable.skills
        ):
            bad.append("code_changes")
        return bad

    def validate_against(self, experiment: Experiment) -> None:
        """Raise ProposalRejectedError if this proposal violates the contract."""
        bad = self.violations_against(experiment.editable)
        if (
            self.search_space_expansion_request is not None
            and not experiment.proposer.allow_search_space_expansion
        ):
            bad.append("search_space_expansion_request")
        if bad:
            raise ProposalRejectedError(bad)


class ProposerInputs(BootstrapModel):
    """Snapshot of what the proposer was looking at when it proposed."""

    type: ProposerStrategy
    strategy_parameters: dict[str, Any] = Field(default_factory=dict)
    iterations_consulted: list[int] = Field(default_factory=list)
    failure_modes_consulted: list[str] = Field(default_factory=list)
    """Stable failure-mode ids the proposer was shown when proposing this
    iteration — the dominant official modes carried over from prior iterations.
    Lets a Proposal.hypothesis say "reduce mode fm_…" and makes the
    before/after on `IterationMetrics.failure_mode_counts` interpretable.
    See docs/spec/error_analysis_design.md §7."""


class ExecutionInfo(BootstrapModel):
    variant_id: NonEmptyStr
    ran_against: dict[str, Any] = Field(default_factory=dict)
    """Snapshot of dataset/case ids actually run (could be a subset of the
    optimization dataset under sample_strategy=stratified)."""
    trace_run_ids: list[NonEmptyStr] = Field(default_factory=list)


class MetricObservation(BootstrapModel):
    name: NonEmptyStr
    value: float
    delta_vs_baseline: float | None = None


class IterationMetrics(BootstrapModel):
    primary: MetricObservation
    guardrails: list[MetricObservation] = Field(default_factory=list)
    reliability: dict[str, float] = Field(default_factory=dict)
    cost_usd: float | None = Field(default=None, ge=0.0)
    duration_seconds: float | None = Field(default=None, ge=0.0)
    failure_mode_counts: dict[str, int] = Field(default_factory=dict)
    """How often each failure mode occurred this iteration, keyed by the stable
    mode identity (a `FailureMode` id once analysis has run, or a raw
    deterministic-grader tag before then). Persisting these is what makes the
    "did this change reduce mode X?" question answerable across iterations.
    See docs/spec/error_analysis_design.md §5."""


class IterationDecision(BootstrapModel):
    outcome: DecisionOutcome
    rationale: NonEmptyStr
    next_action: NonEmptyStr | None = None


class IterationRecord(BaseEntity):
    _id_prefix: ClassVar[str] = "itr"

    experiment_id: NonEmptyStr
    iteration: int = Field(ge=0)
    parent_iteration: int | None = Field(default=None, ge=0)
    state: IterationState
    proposer: ProposerInputs
    hypothesis: NonEmptyStr
    proposed_parameters: dict[str, Any] = Field(default_factory=dict)
    execution: ExecutionInfo
    metrics: IterationMetrics | None = None
    decision: IterationDecision | None = None
    duration_seconds: float | None = Field(default=None, ge=0.0)
    cost_usd: float | None = Field(default=None, ge=0.0)

    @model_validator(mode="after")
    def _completed_requires_metrics_and_decision(self) -> IterationRecord:
        if self.state == IterationState.COMPLETED:
            if self.metrics is None:
                raise ValueError("IterationRecord(state=completed) requires metrics")
            if self.decision is None:
                raise ValueError("IterationRecord(state=completed) requires decision")
        return self

    @model_validator(mode="after")
    def _parent_iteration_must_be_earlier(self) -> IterationRecord:
        if self.parent_iteration is not None and self.parent_iteration >= self.iteration:
            raise ValueError(
                f"parent_iteration ({self.parent_iteration}) must be strictly less "
                f"than iteration ({self.iteration})"
            )
        return self


class HumanRationale(BootstrapModel):
    decided_by: NonEmptyStr
    decided_at: datetime
    notes: str | None = None
    overrides_automated: bool = False


class DecisionRationale(BootstrapModel):
    automated: NonEmptyStr
    human: HumanRationale | None = None


class NextAction(BootstrapModel):
    kind: NonEmptyStr
    """e.g. 'ship_with_flag', 'track_metric', 'open_incident', 'spawn_subexperiment'."""
    payload: dict[str, Any] = Field(default_factory=dict)


class DecisionRecord(BaseEntity):
    _id_prefix: ClassVar[str] = "dec"

    experiment_id: NonEmptyStr
    iteration: int = Field(ge=0)
    variant_id: NonEmptyStr
    outcome: DecisionOutcome
    rationale: DecisionRationale
    metrics_snapshot: dict[str, float] = Field(default_factory=dict)
    affected_artifacts: list[NonEmptyStr] = Field(default_factory=list)
    next_actions: list[NextAction] = Field(default_factory=list)
    superseded_by: str | None = None
