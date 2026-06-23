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

from selfevals.schemas._base import BaseEntity, EntityRef, NonEmptyStr, SelfEvalsModel
from selfevals.schemas.enums import (
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


class MetricTarget(SelfEvalsModel):
    """A scalar metric expectation."""

    name: NonEmptyStr
    operator: ComparisonOp
    value: float


class TargetSpec(SelfEvalsModel):
    """Primary metric to optimize + guardrails that must not regress."""

    primary: MetricTarget
    guardrails: list[MetricTarget] = Field(default_factory=list)
    primary_grader: str | None = None
    """When set, the primary pass-style metric is scored against this single
    grader's verdict instead of the conjunctive worst-of across all graders.
    Lets one experiment optimize toward a specific grader (e.g. `must_include`)
    while other graders still run and report their own pass-rate. `None` keeps
    the default worst-of behaviour, where a case passes only if every grader
    passed."""


class EditableContract(SelfEvalsModel):
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


class SearchSpace(SelfEvalsModel):
    """Parameter spaces the proposer is allowed to sample from."""

    model_params: dict[str, Any] = Field(default_factory=dict)
    prompt_variables: dict[str, Any] = Field(default_factory=dict)
    tool_params: dict[str, Any] = Field(default_factory=dict)


class FrozenSnapshot(SelfEvalsModel):
    """References pinned for the duration of the experiment."""

    fleet: EntityRef
    agents: list[EntityRef] = Field(min_length=1)
    tools: list[EntityRef] = Field(default_factory=list)
    datasets: list[EntityRef] = Field(min_length=1)
    graders: list[EntityRef] = Field(default_factory=list)
    risk_registry: EntityRef | None = None
    feature_registry: EntityRef | None = None


class ProposerSpec(SelfEvalsModel):
    strategy: ProposerStrategy
    allow_search_space_expansion: bool = False
    parameters: dict[str, Any] = Field(default_factory=dict)
    """Strategy-specific config (e.g. grid step sizes, random seed)."""


class ConvergenceSpec(SelfEvalsModel):
    min_delta: float = Field(default=0.005, ge=0.0)
    patience: int = Field(default=3, ge=1)
    early_stop: bool | None = None
    """Whether the loop may stop early on a convergence plateau, overriding the
    per-proposer default.

    The default (`None`) is proposer-aware: open-ended proposers (random / llm)
    early-stop on a plateau, but the **grid** proposer does NOT — enumerating the
    full cartesian product is its whole contract, so a mid-grid plateau must not
    skip the remaining combinations (the "converged after 4/6" trap, where
    `chunking x vector_weight` combos went unprobed).

    Set explicitly to override: `early_stop: true` re-enables the plateau cutoff
    for grid (cheap hill-climbing over a large grid on a tight budget);
    `early_stop: false` forces any proposer to exhaust its space (or hit
    `max_iterations`) regardless of plateaus. `min_delta`/`patience` tune the
    plateau test whenever early-stop is active."""


class DatasetUsage(SelfEvalsModel):
    optimization: EntityRef
    """Dataset proposers may search against."""
    gates: list[EntityRef] = Field(default_factory=list)
    """Datasets used as pass/fail gates (regression, golden, safety)."""


class RetrySpec(SelfEvalsModel):
    """How transient agent-call failures (429/5xx/timeout) are retried.

    Adapter-level retry, distinct from the durable job retry (`RunJob.attempt`):
    this handles per-call blips before the repetition completes; job retry covers
    the whole run/worker dying. ON by default — transient blips are common and
    cheap to absorb, and only `retryable` errors retry, so permanent failures
    still fail fast. `max_retries=0` disables it."""

    max_retries: int = Field(default=2, ge=0, le=10)
    base_delay_seconds: float = Field(default=0.5, gt=0.0, le=60.0)
    max_delay_seconds: float = Field(default=30.0, gt=0.0, le=300.0)
    multiplier: float = Field(default=2.0, ge=1.0, le=10.0)
    jitter: float = Field(default=0.5, ge=0.0, le=1.0)
    """Full-jitter fraction [0,1]: the actual delay is sampled in
    `[expo*(1-jitter), expo]`, so N cases that hit a 429 together wake at
    independent times instead of re-storming the provider in lockstep."""


class RateLimitSpec(SelfEvalsModel):
    """Pre-emptive request throttle, shared across all concurrent cases.

    OFF by default (`requests_per_minute=None`): we can't know the user's
    provider tier, so a wrong default would either throttle needlessly or fail to
    protect. Until the user opts in, the retry layer is the safety net. When set,
    a token bucket caps requests/min before they're sent — the real limiter at
    scale (`parallelism` is only the in-flight ceiling; the two compose, min
    wins). Token-per-minute is reserved for a later version (output tokens are
    unknown until the response, so TPM can't be pre-charged accurately)."""

    requests_per_minute: int | None = Field(default=None, ge=1, le=100_000)
    burst: int | None = Field(default=None, ge=1, le=100_000)
    """Bucket capacity (max burst before throttling kicks in). None → ~1s of
    `requests_per_minute` worth of burst."""


class RunSpec(SelfEvalsModel):
    sandbox: SandboxMode
    runtime: RuntimeLocation = RuntimeLocation.OFFLINE
    sample_strategy: Literal["full", "stratified", "random_subset"] = "full"
    max_iterations: int = Field(default=20, ge=1, le=10000)
    repetitions_per_case: int = Field(default=1, ge=1, le=100)
    parallelism: int = Field(default=8, ge=1, le=64)
    convergence: ConvergenceSpec = Field(default_factory=ConvergenceSpec)
    retry: RetrySpec = Field(default_factory=RetrySpec)
    rate_limit: RateLimitSpec = Field(default_factory=RateLimitSpec)
    seed: int | None = None
    persist_traces: Literal["none", "all", "failed"] = "failed"
    """Which per-repetition traces the loop writes to storage:
    `none` (keep the DB small — traces aren't queryable afterward),
    `all` (full observability), or `failed` (default — only traces that
    errored or got a failing grade, the ones error analysis needs). Persisted
    traces carry their grader results, so `analyze pull` can classify them
    without re-running the agent. See docs/spec/error_analysis_design.md §5."""


class JudgePanel(SelfEvalsModel):
    members: list[NonEmptyStr] = Field(default_factory=list)
    consensus_rule: Literal["majority", "unanimous", "weighted"] = "majority"


class CounterfactualSpec(SelfEvalsModel):
    enabled: bool = False
    generation_strategy: Literal["paraphrase", "manual"] = "paraphrase"
    pairs_per_case: int = Field(default=3, ge=1)
    max_score_variance: float = Field(default=0.05, ge=0.0, le=1.0)


class HumanSpotCheckSpec(SelfEvalsModel):
    enabled: bool = False
    sample_rate: float = Field(default=0.05, ge=0.0, le=1.0)
    trigger_on_jump: float = Field(default=0.1, ge=0.0, le=1.0)


class OutcomeMetricsSpec(SelfEvalsModel):
    """Real-world outcome signals required for live_canary runs."""

    metrics: list[NonEmptyStr] = Field(min_length=1)
    """e.g. ['human_approval_rate', 'escalation_rate', 'task_completion']"""


class JudgeDefenses(SelfEvalsModel):
    """Anti-judge-hacking levers. Canon §13, operational §C.2."""

    holdout_visible_to_proposer: bool = False
    overfit_penalty_max_delta: float = Field(default=0.05, ge=0.0, le=1.0)
    panel: JudgePanel | None = None
    counterfactuals: CounterfactualSpec = Field(default_factory=CounterfactualSpec)
    human_spot_check: HumanSpotCheckSpec = Field(default_factory=HumanSpotCheckSpec)
    adversarial_dataset: EntityRef | None = None
    outcome_metrics: OutcomeMetricsSpec | None = None


class ReliabilitySpec(SelfEvalsModel):
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


class DecisionPolicy(SelfEvalsModel):
    if_regression_fails: Literal["reject", "investigate", "spawn_subexperiment"] = "reject"
    if_guardrail_fails: Literal["reject", "require_tradeoff_review"] = "require_tradeoff_review"
    if_judge_human_disagree: Literal["escalate_to_calibration", "investigate"] = (
        "escalate_to_calibration"
    )


class AnalysisTriggerSpec(SelfEvalsModel):
    """When selfevals should stage an error-analysis bundle.

    `fail_rate_above` is the only trigger in v1: stage only when the
    iteration's fraction of failed cases exceeds `threshold`. This is the
    sample-size instinct — don't spend an agent's coding effort on a healthy
    run. See docs/spec/error_analysis_design.md §9.
    """

    when: Literal["fail_rate_above"] = "fail_rate_above"
    threshold: float = Field(default=0.10, ge=0.0, le=1.0)


class ErrorAnalysisSpec(SelfEvalsModel):
    """Declarative opt-in for the continuous error-analysis loop (§9).

    Declarative and governable, not a boolean afterthought: the intent lives
    with the experiment and is reviewable in the diff. selfevals stages a
    bundle (and records that the trigger fired) when the trigger condition
    holds; it never invokes an agent or an LLM itself — agents own the
    intelligence, selfevals owns the data + contract.
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


class ExperimentTaxonomy(SelfEvalsModel):
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
    def _proposer_strategy_is_implemented(self) -> Experiment:
        # Reject strategies we don't yet implement: declaring one would
        # silently no-op. bayesian/bandit/evolutionary remain reserved.
        implemented = {
            ProposerStrategy.MANUAL,
            ProposerStrategy.GRID,
            ProposerStrategy.RANDOM,
            ProposerStrategy.LLM_PROPOSER,
        }
        if self.proposer.strategy not in implemented:
            raise ValueError(
                f"proposer.strategy={self.proposer.strategy} is not implemented; "
                "use manual, grid, random, or llm_proposer"
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
