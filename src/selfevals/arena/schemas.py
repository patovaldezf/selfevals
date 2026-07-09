"""Wire shapes for the Arena bundle — the contract an external coding agent
(Claude Code or similar) reads to decide what code variant to try next.

Mirrors `analysis/schemas.py`'s role for error-analysis: pure Pydantic view
models, no storage or business logic. `arena/bundle.py` is the only producer.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class BundleMetrics(BaseModel):
    """One variant's latest scored result — a thin projection of
    `IterationMetrics` (primary value, guardrails, cost, failure modes)."""

    experiment_id: str
    iteration_id: str
    primary_metric: str
    primary_value: float
    guardrails: dict[str, float] = Field(default_factory=dict)
    reliability: dict[str, float] = Field(default_factory=dict)
    cost_usd: float | None = None
    duration_seconds: float | None = None
    error_rate: float = 0.0
    failure_mode_counts: dict[str, int] = Field(default_factory=dict)


class RoundHistoryPoint(BaseModel):
    round: int
    primary_value: float | None = None
    cost_usd: float | None = None


class VariantCard(BaseModel):
    variant_id: str
    name: str
    git_ref: str
    resolved_sha: str | None = None
    hypothesis: str | None = None
    state: str
    error: str | None = None
    latest_result: BundleMetrics | None = None
    history: list[RoundHistoryPoint] = Field(default_factory=list)


class LeaderboardRow(BaseModel):
    rank: int
    variant_id: str
    name: str
    primary_value: float | None = None
    delta_vs_best: float | None = None
    cost_usd: float | None = None


class CompareParamRowView(BaseModel):
    key: str
    a: object | None = None
    b: object | None = None
    changed: bool = False


class CompareMetricRowView(BaseModel):
    name: str
    a: float | None = None
    b: float | None = None
    delta: float | None = None


class PairwiseVsBest(BaseModel):
    """`compute_compare(best_iteration, this_variant_iteration)`, projected."""

    variant_id: str
    name: str
    proposal_diff: list[CompareParamRowView] = Field(default_factory=list)
    metrics_diff: list[CompareMetricRowView] = Field(default_factory=list)
    only_best: dict[str, int] = Field(default_factory=dict)
    only_this: dict[str, int] = Field(default_factory=dict)
    common_failure_modes: dict[str, list[int]] = Field(default_factory=dict)
    """mode -> [count_in_best, count_in_this]."""
    recommendation_kind: str
    winner: str | None = None


class BundleErrorSpanView(BaseModel):
    kind: str
    name: str
    error: str | None = None


class BundleTraceView(BaseModel):
    trace_id: str
    grade_label: str
    first_error_span: BundleErrorSpanView | None = None


class ExemplarFailures(BaseModel):
    variant_id: str
    name: str
    traces: list[BundleTraceView] = Field(default_factory=list)


class ArenaSummary(BaseModel):
    id: str
    name: str
    goal: str
    objective_metric: str
    current_round: int
    state: str
    budget_rounds_remaining: int | None = None
    budget_variants_remaining: int | None = None


class ArenaContract(BaseModel):
    """URL templates the agent fills in with `{arena_id}` / `{round_id}` /
    `{variant_id}` — keeps the loop's write operations discoverable from the
    bundle itself, without a separate API reference lookup."""

    register_variant: str
    launch_round: str
    poll_round: str
    promote: str


class ArenaConvergence(BaseModel):
    """Plateau signal over the arena's best-per-round value, so the driving
    agent (or a human) doesn't have to eyeball `history` to decide whether to
    keep proposing variants. Same semantics as an experiment's
    `run.convergence` (`optimization.loop.has_converged`): the best value
    hasn't improved by `min_delta` in the last `patience` rounds."""

    converged: bool
    rounds_observed: int
    min_delta: float
    patience: int
    best_value: float | None = None


class ArenaBundle(BaseModel):
    arena: ArenaSummary
    round: int | None = None
    variants: list[VariantCard] = Field(default_factory=list)
    leaderboard: list[LeaderboardRow] = Field(default_factory=list)
    pairwise_vs_best: list[PairwiseVsBest] = Field(default_factory=list)
    exemplar_failures: list[ExemplarFailures] = Field(default_factory=list)
    convergence: ArenaConvergence
    contract: ArenaContract
