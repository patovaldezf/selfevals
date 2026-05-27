"""Experiment: the contract that drives an optimization run.

An Experiment declares:
- what to optimize (target metric + guardrails)
- what can change (`editable` contract — bool flags per knob)
- how to propose changes (strategy + search space)
- how to run (sandbox, repetitions, parallelism)
- how judges are defended (holdout, panel, counterfactuals, ...)
- how decisions are made (rules + outcome routing)

Contracts enforced here:
1. editable.tool_code | editable.workflow_graph | editable.skills implies
   mode == agent_loop (operational §A.1.5).
2. run.sandbox == live_canary implies judge_defenses.outcome_metrics is
   set (canon §13, operational §C.2).
3. Experiment state machine — only legal transitions are allowed via
   `transition_to()`.
4. proposer.allow_search_space_expansion defaults to false.
5. reliability metrics are validated as `pass@k`, `pass^k`, or known
   reliability metric names.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar, Literal

from pydantic import Field, field_validator, model_validator

from selfeval.schemas._base import BaseEntity, EntityRef, NonEmptyStr, SelfEvalModel
from selfeval.schemas.enums import (
    DatasetType,
    ExperimentState,
    Mode,
    ProposerStrategy,
    RuntimeLocation,
    SandboxMode,
)

ComparisonOp = Literal[">", ">=", "<", "<=", "=="]

_RELIABILITY_METRIC_RE = re.compile(
    r"^("
    r"pass@\d+"
    r"|pass\^\d+"
    r"|consistency_rate"
    r"|stability_score"
    r"|recovery_rate"
    r")$"
)


class MetricTarget(SelfEvalModel):
    """A scalar metric expectation."""

    name: NonEmptyStr
    operator: ComparisonOp
    value: float


class TargetSpec(SelfEvalModel):
    """Primary metric to optimize + guardrails that must not regress."""

    primary: MetricTarget
    guardrails: list[MetricTarget] = Field(default_factory=list)


class EditableContract(SelfEvalModel):
    """What an experiment is allowed to change.

    Fields are contractual: a Proposal that mutates a field set to False is
    rejected. Tool/workflow/skill code mutations require mode=agent_loop.
    """

    prompt: bool = True
    model_params: bool = True
    model_choice: bool = False
    tool_descriptions: bool = False
    tool_code: bool = False
    workflow_graph: bool = False
    skills: bool = False
    dataset: bool = False
    graders: bool = False

    def fields_requiring_agent_loop(self) -> list[str]:
        triggers = []
        if self.tool_code:
            triggers.append("tool_code")
        if self.workflow_graph:
            triggers.append("workflow_graph")
        if self.skills:
            triggers.append("skills")
        return triggers


class SearchSpace(SelfEvalModel):
    """Parameter spaces the proposer is allowed to sample from."""

    model_params: dict[str, Any] = Field(default_factory=dict)
    prompt_variables: dict[str, Any] = Field(default_factory=dict)
    tool_params: dict[str, Any] = Field(default_factory=dict)


class FrozenSnapshot(SelfEvalModel):
    """References pinned for the duration of the experiment."""

    fleet: EntityRef
    agents: list[EntityRef] = Field(min_length=1)
    tools: list[EntityRef] = Field(default_factory=list)
    datasets: list[EntityRef] = Field(min_length=1)
    graders: list[EntityRef] = Field(default_factory=list)
    risk_registry: EntityRef | None = None
    feature_registry: EntityRef | None = None


class ProposerSpec(SelfEvalModel):
    strategy: ProposerStrategy
    allow_search_space_expansion: bool = False
    parameters: dict[str, Any] = Field(default_factory=dict)
    """Strategy-specific config (e.g. grid step sizes, random seed)."""


class ConvergenceSpec(SelfEvalModel):
    min_delta: float = Field(default=0.005, ge=0.0)
    patience: int = Field(default=3, ge=1)


class DatasetUsage(SelfEvalModel):
    optimization: EntityRef
    """Dataset proposers may search against."""
    gates: list[EntityRef] = Field(default_factory=list)
    """Datasets used as pass/fail gates (regression, golden, safety)."""


class RunSpec(SelfEvalModel):
    sandbox: SandboxMode
    runtime: RuntimeLocation = RuntimeLocation.OFFLINE
    sample_strategy: Literal["full", "stratified", "random_subset"] = "full"
    max_iterations: int = Field(default=20, ge=1, le=10000)
    repetitions_per_case: int = Field(default=1, ge=1, le=100)
    parallelism: int = Field(default=1, ge=1, le=64)
    convergence: ConvergenceSpec = Field(default_factory=ConvergenceSpec)
    seed: int | None = None
    persist_traces: Literal["none", "all", "failed"] = "failed"
    """Which per-repetition traces the loop writes to storage:
    `none` (keep the DB small — traces aren't queryable afterward),
    `all` (full observability), or `failed` (default — only traces that
    errored or got a failing grade, the ones error analysis needs). Persisted
    traces carry their grader results, so `analyze pull` can classify them
    without re-running the agent. See docs/spec/error_analysis_design.md §5."""


class JudgePanel(SelfEvalModel):
    members: list[NonEmptyStr] = Field(default_factory=list)
    consensus_rule: Literal["majority", "unanimous", "weighted"] = "majority"


class CounterfactualSpec(SelfEvalModel):
    enabled: bool = False
    generation_strategy: Literal["paraphrase", "manual"] = "paraphrase"
    pairs_per_case: int = Field(default=3, ge=1)
    max_score_variance: float = Field(default=0.05, ge=0.0, le=1.0)


class HumanSpotCheckSpec(SelfEvalModel):
    enabled: bool = False
    sample_rate: float = Field(default=0.05, ge=0.0, le=1.0)
    trigger_on_jump: float = Field(default=0.1, ge=0.0, le=1.0)


class OutcomeMetricsSpec(SelfEvalModel):
    """Real-world outcome signals required for live_canary runs."""

    metrics: list[NonEmptyStr] = Field(min_length=1)
    """e.g. ['human_approval_rate', 'escalation_rate', 'task_completion']"""


class JudgeDefenses(SelfEvalModel):
    """Anti-judge-hacking levers. Canon §13, operational §C.2."""

    holdout_visible_to_proposer: bool = False
    overfit_penalty_max_delta: float = Field(default=0.05, ge=0.0, le=1.0)
    panel: JudgePanel | None = None
    counterfactuals: CounterfactualSpec = Field(default_factory=CounterfactualSpec)
    human_spot_check: HumanSpotCheckSpec = Field(default_factory=HumanSpotCheckSpec)
    adversarial_dataset: EntityRef | None = None
    outcome_metrics: OutcomeMetricsSpec | None = None


class ReliabilitySpec(SelfEvalModel):
    repetitions_per_case: int = Field(default=1, ge=1, le=100)
    metrics: list[NonEmptyStr] = Field(default_factory=list)
    """e.g. ['pass@1', 'pass^3', 'consistency_rate']"""

    @field_validator("metrics")
    @classmethod
    def _valid_metric_names(cls, value: list[str]) -> list[str]:
        for m in value:
            if not _RELIABILITY_METRIC_RE.match(m):
                raise ValueError(
                    f"reliability metric {m!r} must match pass@N, pass^N, "
                    "consistency_rate, stability_score, or recovery_rate"
                )
        return value


class DecisionPolicy(SelfEvalModel):
    if_regression_fails: Literal["reject", "investigate", "spawn_subexperiment"] = "reject"
    if_guardrail_fails: Literal["reject", "require_tradeoff_review"] = "require_tradeoff_review"
    if_judge_human_disagree: Literal["escalate_to_calibration", "investigate"] = (
        "escalate_to_calibration"
    )


class AnalysisTriggerSpec(SelfEvalModel):
    """When selfeval should stage an error-analysis bundle.

    `fail_rate_above` is the only trigger in v1: stage only when the
    iteration's fraction of failed cases exceeds `threshold`. This is the
    sample-size instinct — don't spend an agent's coding effort on a healthy
    run. See docs/spec/error_analysis_design.md §9.
    """

    when: Literal["fail_rate_above"] = "fail_rate_above"
    threshold: float = Field(default=0.10, ge=0.0, le=1.0)


class ErrorAnalysisSpec(SelfEvalModel):
    """Declarative opt-in for the continuous error-analysis loop (§9).

    Declarative and governable, not a boolean afterthought: the intent lives
    with the experiment and is reviewable in the diff. selfeval stages a
    bundle (and records that the trigger fired) when the trigger condition
    holds; it never invokes an agent or an LLM itself — agents own the
    intelligence, selfeval owns the data + contract.
    """

    enabled: bool = False
    taxonomy: Literal["workspace"] = "workspace"
    """Which taxonomy to classify against. Only per-workspace in v1."""
    trigger: AnalysisTriggerSpec = Field(default_factory=AnalysisTriggerSpec)
    scope: Literal["failed_only", "all"] = "failed_only"

    def should_stage(self, *, fail_rate: float) -> bool:
        """True when this iteration warrants staging an analysis bundle."""
        if not self.enabled:
            return False
        if self.trigger.when == "fail_rate_above":
            return fail_rate > self.trigger.threshold
        return False


class ExperimentTaxonomy(SelfEvalModel):
    """High-level classification of an Experiment."""

    target_features: list[NonEmptyStr] = Field(min_length=1)
    target_levels: list[NonEmptyStr] = Field(default_factory=list)
    dataset_types: list[DatasetType] = Field(min_length=1)

    @field_validator("target_features", "target_levels", "dataset_types")
    @classmethod
    def _unique(cls, value: list[Any]) -> list[Any]:
        if len(set(value)) != len(value):
            raise ValueError("entries must be unique")
        return value


# State machine — legal transitions. Operational §A.2.
_LEGAL_TRANSITIONS: dict[ExperimentState, set[ExperimentState]] = {
    ExperimentState.DRAFT: {ExperimentState.QUEUED, ExperimentState.ABORTED},
    ExperimentState.QUEUED: {
        ExperimentState.RUNNING,
        ExperimentState.ABORTED,
        ExperimentState.DRAFT,
    },
    ExperimentState.RUNNING: {
        ExperimentState.PAUSED,
        ExperimentState.COMPLETED,
        ExperimentState.ABORTED,
    },
    ExperimentState.PAUSED: {
        ExperimentState.RUNNING,
        ExperimentState.ABORTED,
        ExperimentState.COMPLETED,
    },
    ExperimentState.COMPLETED: {ExperimentState.SUPERSEDED},
    ExperimentState.ABORTED: set(),
    ExperimentState.SUPERSEDED: set(),
}


class IllegalStateTransitionError(ValueError):
    """Raised when an Experiment attempts an out-of-table state transition."""


class Experiment(BaseEntity):
    _id_prefix: ClassVar[str] = "exp"

    name: NonEmptyStr
    goal: NonEmptyStr
    mode: Mode
    taxonomy: ExperimentTaxonomy
    datasets: DatasetUsage
    target: TargetSpec
    editable: EditableContract = Field(default_factory=EditableContract)
    search_space: SearchSpace = Field(default_factory=SearchSpace)
    frozen: FrozenSnapshot
    proposer: ProposerSpec
    run: RunSpec
    judge_defenses: JudgeDefenses = Field(default_factory=JudgeDefenses)
    reliability: ReliabilitySpec = Field(default_factory=ReliabilitySpec)
    decision: DecisionPolicy = Field(default_factory=DecisionPolicy)
    error_analysis: ErrorAnalysisSpec = Field(default_factory=ErrorAnalysisSpec)
    state: ExperimentState = ExperimentState.DRAFT
    content_hash: str | None = None

    @model_validator(mode="after")
    def _editable_requires_agent_loop(self) -> Experiment:
        triggers = self.editable.fields_requiring_agent_loop()
        if triggers and self.mode != Mode.AGENT_LOOP:
            raise ValueError(f"editable {triggers} requires mode=agent_loop; got mode={self.mode}")
        return self

    @model_validator(mode="after")
    def _live_canary_requires_outcome_metrics(self) -> Experiment:
        if (
            self.run.sandbox == SandboxMode.LIVE_CANARY
            and self.judge_defenses.outcome_metrics is None
        ):
            raise ValueError("run.sandbox=live_canary requires judge_defenses.outcome_metrics")
        return self

    @model_validator(mode="after")
    def _proposer_strategy_implemented_in_mvp(self) -> Experiment:
        # MVP only ships manual/grid/random; declaring a post-MVP strategy
        # would silently no-op. Reject at schema time.
        mvp_strategies = {
            ProposerStrategy.MANUAL,
            ProposerStrategy.GRID,
            ProposerStrategy.RANDOM,
        }
        if self.proposer.strategy not in mvp_strategies:
            raise ValueError(
                f"proposer.strategy={self.proposer.strategy} is reserved for "
                "post-MVP; use manual, grid, or random"
            )
        return self

    def transition_to(self, new_state: ExperimentState) -> None:
        """Mutate `state` if `new_state` is a legal successor.

        Raises `IllegalStateTransitionError` otherwise.
        """
        legal = _LEGAL_TRANSITIONS[self.state]
        if new_state not in legal:
            raise IllegalStateTransitionError(
                f"cannot transition Experiment from {self.state} to {new_state}; "
                f"legal: {sorted(s.value for s in legal)}"
            )
        self.state = new_state

    def is_terminal(self) -> bool:
        return self.state in (
            ExperimentState.COMPLETED,
            ExperimentState.ABORTED,
            ExperimentState.SUPERSEDED,
        )
