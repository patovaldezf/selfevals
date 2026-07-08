"""Build an ArenaBundle — the cross-variant view a coding agent reads to
decide what to try next.

Pure read: never mutates storage. Every variant's "latest result" is the
single `IterationRecord` of its most recent completed round (each
(variant, round) is a fresh single-iteration Experiment, per Arena's design
— see `arena/service.py`). Reuses `reporter.compare.compute_compare`
unmodified for the pairwise-vs-best comparisons, and the same trace-grading
helpers `analysis/bundle.py` uses for exemplar failures — no new math.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from selfevals.analysis.bundle import first_error_span, grade, is_failed
from selfevals.arena.schemas import (
    ArenaBundle,
    ArenaContract,
    ArenaSummary,
    BundleErrorSpanView,
    BundleMetrics,
    BundleTraceView,
    CompareMetricRowView,
    CompareParamRowView,
    ExemplarFailures,
    LeaderboardRow,
    PairwiseVsBest,
    RoundHistoryPoint,
    VariantCard,
)
from selfevals.reporter.compare import compute_compare
from selfevals.schemas.arena import Arena, ArenaRound, ArenaVariant
from selfevals.schemas.iteration import IterationRecord
from selfevals.schemas.trace import Trace
from selfevals.storage.interface import ListFilter

if TYPE_CHECKING:
    from selfevals.storage.interface import StorageInterface, WorkspaceScope

_EXEMPLAR_LIMIT = 3


def _latest_iteration(scope: WorkspaceScope, experiment_id: str) -> IterationRecord | None:
    """The single completed iteration of a (variant, round) child experiment.

    Arena launches each child with `max_iterations=1`, so at most one
    `IterationRecord` exists per experiment — this is not "pick the best of
    many", just "read the one that exists, if it finished."
    """
    records = [
        it
        for it in scope.list_entities(
            IterationRecord, ListFilter(where={"experiment_id": experiment_id})
        )
        if isinstance(it, IterationRecord)
    ]
    return records[0] if records else None


def _bundle_metrics(experiment_id: str, record: IterationRecord) -> BundleMetrics | None:
    if record.metrics is None:
        return None
    m = record.metrics
    return BundleMetrics(
        experiment_id=experiment_id,
        iteration_id=record.id,
        primary_metric=m.primary.name,
        primary_value=m.primary.value,
        guardrails={g.name: g.value for g in m.guardrails},
        reliability=dict(m.reliability),
        cost_usd=m.cost_usd,
        duration_seconds=m.duration_seconds,
        error_rate=m.error_rate,
        failure_mode_counts=dict(m.failure_mode_counts),
    )


def _history(scope: WorkspaceScope, arena_id: str, variant_id: str) -> list[RoundHistoryPoint]:
    rounds = [
        r
        for r in scope.list_entities(
            ArenaRound, ListFilter(where={"arena_id": arena_id}, order_by="index", order_desc=False)
        )
        if isinstance(r, ArenaRound)
    ]
    points: list[RoundHistoryPoint] = []
    for round_ in rounds:
        entry = next((e for e in round_.entries if e.variant_id == variant_id), None)
        if entry is None or entry.experiment_id is None:
            continue
        record = _latest_iteration(scope, entry.experiment_id)
        if record is None or record.metrics is None:
            points.append(RoundHistoryPoint(round=round_.index))
            continue
        points.append(
            RoundHistoryPoint(
                round=round_.index,
                primary_value=record.metrics.primary.value,
                cost_usd=record.metrics.cost_usd,
            )
        )
    return points


def _pairwise_vs_best(best: VariantCard, best_record: IterationRecord, other: VariantCard, other_record: IterationRecord) -> PairwiseVsBest:
    result = compute_compare(best_record, other_record)
    rec = result.recommendation
    return PairwiseVsBest(
        variant_id=other.variant_id,
        name=other.name,
        proposal_diff=[
            CompareParamRowView(key=r.key, a=r.a, b=r.b, changed=r.changed)
            for r in result.proposal_diff
        ],
        metrics_diff=[
            CompareMetricRowView(name=r.name, a=r.a, b=r.b, delta=r.delta)
            for r in result.metrics_diff
        ],
        only_best=dict(result.failure_modes.only_a),
        only_this=dict(result.failure_modes.only_b),
        common_failure_modes={k: list(v) for k, v in result.failure_modes.common.items()},
        recommendation_kind=rec.kind,
        winner=rec.winner,
    )


def _exemplar_failures(scope: WorkspaceScope, variant: ArenaVariant, experiment_id: str) -> ExemplarFailures:
    traces = [
        t
        for t in scope.list_entities(Trace, ListFilter(where={"run.experiment_id": experiment_id}))
        if isinstance(t, Trace)
    ]
    views: list[BundleTraceView] = []
    for trace in traces:
        if not is_failed(trace):
            continue
        if len(views) >= _EXEMPLAR_LIMIT:
            break
        trace_grade = grade(trace)
        error_span = first_error_span(trace)
        views.append(
            BundleTraceView(
                trace_id=trace.id,
                grade_label=trace_grade.label,
                first_error_span=(
                    BundleErrorSpanView(kind=error_span.kind, name=error_span.name, error=error_span.error)
                    if error_span is not None
                    else None
                ),
            )
        )
    return ExemplarFailures(variant_id=variant.id, name=variant.name, traces=views)


def _round_entries_for(
    scope: WorkspaceScope, arena_id: str, target_round: int | None
) -> dict[str, str]:
    """variant_id -> experiment_id for the entries of one round, or {} if
    `target_round` is None/negative (no round launched yet) or not found."""
    if target_round is None or target_round < 0:
        return {}
    rounds = [
        r
        for r in scope.list_entities(
            ArenaRound, ListFilter(where={"arena_id": arena_id, "index_": target_round})
        )
        if isinstance(r, ArenaRound)
    ]
    if not rounds:
        return {}
    return {e.variant_id: e.experiment_id for e in rounds[0].entries if e.experiment_id is not None}


def _build_variant_cards(
    scope: WorkspaceScope, arena_id: str, variants: list[ArenaVariant], round_entries: dict[str, str]
) -> tuple[list[VariantCard], dict[str, tuple[str, IterationRecord]]]:
    """One `VariantCard` per variant, plus the (experiment_id, IterationRecord)
    behind each card's `latest_result` — kept alongside for the pairwise/
    exemplar-failures passes, which need the raw record, not just its metrics."""
    cards: list[VariantCard] = []
    records_by_variant: dict[str, tuple[str, IterationRecord]] = {}
    for variant in variants:
        latest_result = None
        experiment_id = round_entries.get(variant.id)
        if experiment_id is not None:
            record = _latest_iteration(scope, experiment_id)
            if record is not None:
                records_by_variant[variant.id] = (experiment_id, record)
                latest_result = _bundle_metrics(experiment_id, record)
        cards.append(
            VariantCard(
                variant_id=variant.id,
                name=variant.name,
                git_ref=variant.git_ref,
                resolved_sha=variant.resolved_sha,
                hypothesis=variant.hypothesis,
                state=str(variant.state),
                error=variant.error,
                latest_result=latest_result,
                history=_history(scope, arena_id, variant.id),
            )
        )
    return cards, records_by_variant


def _leaderboard_and_analysis(
    scope: WorkspaceScope,
    cards: list[VariantCard],
    records_by_variant: dict[str, tuple[str, IterationRecord]],
    by_id: dict[str, ArenaVariant],
) -> tuple[list[LeaderboardRow], list[PairwiseVsBest], list[ExemplarFailures]]:
    """Rank scored variants, diff each loser against the best, and pull each
    scored variant's exemplar failures — the three cross-variant views the
    agent reads to decide what to try next."""
    scored: list[tuple[VariantCard, BundleMetrics]] = [
        (c, c.latest_result) for c in cards if c.latest_result is not None
    ]
    scored.sort(key=lambda pair: pair[1].primary_value, reverse=True)
    best_value = scored[0][1].primary_value if scored else None
    leaderboard = [
        LeaderboardRow(
            rank=i + 1,
            variant_id=card.variant_id,
            name=card.name,
            primary_value=metrics.primary_value,
            delta_vs_best=(
                metrics.primary_value - best_value if best_value is not None else None
            ),
            cost_usd=metrics.cost_usd,
        )
        for i, (card, metrics) in enumerate(scored)
    ]

    pairwise: list[PairwiseVsBest] = []
    if scored:
        best_card, _ = scored[0]
        _, best_record = records_by_variant[best_card.variant_id]
        for card, _metrics in scored[1:]:
            _, other_record = records_by_variant[card.variant_id]
            pairwise.append(_pairwise_vs_best(best_card, best_record, card, other_record))

    exemplars = [
        _exemplar_failures(scope, by_id[variant_id], experiment_id)
        for variant_id, (experiment_id, _record) in records_by_variant.items()
    ]
    return leaderboard, pairwise, exemplars


def build_bundle(
    storage: StorageInterface,
    *,
    workspace_id: str,
    arena_id: str,
    round_index: int | None = None,
) -> ArenaBundle:
    """Assemble the bundle for `arena_id`.

    `round_index` selects which round's results to project (defaults to the
    most recently launched round, `arena.current_round - 1`); the bundle
    always lists every variant regardless of round so the agent sees ones
    still preparing or that never got a slot this round.
    """
    with storage.open(workspace_id) as scope:
        arena = scope.get_entity(Arena, arena_id)
        assert isinstance(arena, Arena)

        variants = [
            v
            for v in scope.list_entities(ArenaVariant, ListFilter(where={"arena_id": arena_id}))
            if isinstance(v, ArenaVariant)
        ]

        target_round = round_index if round_index is not None else arena.current_round - 1
        round_entries = _round_entries_for(scope, arena_id, target_round)
        cards, records_by_variant = _build_variant_cards(scope, arena_id, variants, round_entries)
        by_id = {v.id: v for v in variants}
        leaderboard, pairwise, exemplars = _leaderboard_and_analysis(
            scope, cards, records_by_variant, by_id
        )

        budget_rounds_remaining = (
            max(0, arena.budget.max_rounds - arena.current_round)
            if arena.budget.max_rounds is not None
            else None
        )
        budget_variants_remaining = (
            max(0, arena.budget.max_variants - len(variants))
            if arena.budget.max_variants is not None
            else None
        )

    return ArenaBundle(
        arena=ArenaSummary(
            id=arena.id,
            name=arena.name,
            goal=arena.goal,
            objective_metric=arena.objective_metric,
            current_round=arena.current_round,
            state=str(arena.state),
            budget_rounds_remaining=budget_rounds_remaining,
            budget_variants_remaining=budget_variants_remaining,
        ),
        round=target_round if target_round is not None and target_round >= 0 else None,
        variants=cards,
        leaderboard=leaderboard,
        pairwise_vs_best=pairwise,
        exemplar_failures=exemplars,
        contract=ArenaContract(
            register_variant=f"/api/workspaces/{workspace_id}/arenas/{arena_id}/variants",
            launch_round=f"/api/workspaces/{workspace_id}/arenas/{arena_id}/rounds",
            poll_round=f"/api/workspaces/{workspace_id}/arenas/{arena_id}/rounds/{{round_id}}",
            promote=f"/api/workspaces/{workspace_id}/arenas/{arena_id}/promote",
        ),
    )
