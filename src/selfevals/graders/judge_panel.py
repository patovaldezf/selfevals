"""JudgePanelGrader: combine N judges into one authoritative verdict.

This grader composes a panel of sub-graders (typically `LLMJudgeGrader`,
but any `Grader` works) and turns their per-judge verdicts into a single
authoritative `GradeResult` via a consensus rule. It is the consumer that
finally wires up the anti-judge-hacking levers declared on
`JudgeDefenses` (panel / counterfactuals / human_spot_check), which were
defined in the schema but never read by any runtime component until now.

What the panel does, in order:

1. Run every member judge concurrently (`asyncio.gather`) over the same
   `GraderContext`. The grader reuses each member's own `grade` coroutine
   verbatim, so an `LLMJudgeGrader` keeps its rubric, card-gating and JSON
   parsing; the panel never reimplements judging.
2. Combine the member labels into one authoritative `label` + `score`:
   - `majority`: most common non-excluded label wins (ties resolve toward
     the more conservative verdict: fail > partial > pass).
   - `unanimous`: all non-excluded judges must agree on `pass`, otherwise
     the most conservative observed label wins (a single `fail` flips the
     panel).
   - `weighted`: each judge's label contributes its `weight`; the
     heaviest-weighted label wins.
   `ERROR`/`SKIPPED` member verdicts are excluded from the vote. If every
   member is excluded the panel returns `ERROR`.
3. Counterfactual variance (optional): paraphrase / manual variants of the
   case input are re-judged and the variance of the authoritative score is
   measured. High variance is advisory only — it adds a `weight=0`
   breakdown child and degrades `confidence`, but never flips the label.
   Variants are linked via `TraceLink(kind="paraphrase_variant")` recorded
   in `details`.
4. Human spot-check (optional): a seeded sampler decides whether this case
   is selected for human review and, if so, emits `Annotation` stubs in
   `details`. This is non-blocking — it never changes the verdict.
5. A root `BreakdownNode` keyed `judge_panel` with one child per member
   judge plus a `weight=0` `counterfactual_variance` child.
6. Calibration (optional, advisory): when a reachable human label exists on
   `case.context`, the panel reuses `compute_classification_metrics` to
   attach an advisory `CalibrationReport` summary to `details`.

The grader is agnostic: it has no external consumers and zero coupling to
storage, the CLI, or the optimizer. Everything stays async-first; member
judges are awaited via `asyncio.gather`.

Consensus/breakdown/spot-check/calibration logic is split into
`judge_panel_consensus.py` (same free-function-delegate pattern used across
the codebase's other oversized classes) — this class wires that logic
together, passing its config (consensus rule, weights, rng seed, ...) in.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from selfevals.graders.base import BreakdownNode, GradeLabel, Grader, GraderContext, GradeResult
from selfevals.graders.judge_panel_consensus import (
    VOTING_LABELS,
    MemberOutcome,
    aggregate_confidence,
    build_breakdown,
    build_reason,
    build_variant_contexts,
    calibration,
    combine_labels,
    combine_scores,
    member_details,
    population_variance,
    spot_check,
)

if TYPE_CHECKING:
    from selfevals.schemas.experiment import JudgeDefenses

ConsensusRule = str  # "majority" | "unanimous" | "weighted"

_CONSENSUS_RULES = frozenset({"majority", "unanimous", "weighted"})


@dataclass(frozen=True)
class CounterfactualConfig:
    """Local, schema-free view of CounterfactualSpec for the grader."""

    enabled: bool = False
    generation_strategy: str = "paraphrase"
    pairs_per_case: int = 3
    max_score_variance: float = 0.05


@dataclass(frozen=True)
class HumanSpotCheckConfig:
    """Local, schema-free view of HumanSpotCheckSpec for the grader."""

    enabled: bool = False
    sample_rate: float = 0.05
    trigger_on_jump: float = 0.1


def _default_paraphraser(text: str, *, variant_index: int) -> str:
    """Deterministic, dependency-free paraphraser used in tests.

    Produces a stable textual variant of the input without changing its
    meaning enough to flip a calibrated judge. Callers inject a real
    paraphraser in production; this keeps the panel testable offline.
    """

    prefixes = ["", "Please consider: ", "To clarify, ", "In other words, "]
    prefix = prefixes[variant_index % len(prefixes)]
    return f"{prefix}{text}"


@dataclass(frozen=True)
class _CounterfactualResult:
    node: BreakdownNode
    details: dict[str, Any]
    high_variance: bool


class JudgePanelGrader(Grader):
    """Consensus over a panel of judge graders, with judge-defense levers."""

    def __init__(
        self,
        name: str,
        *,
        judges: list[Grader],
        consensus_rule: ConsensusRule = "majority",
        weights: list[float] | None = None,
        counterfactuals: CounterfactualConfig | None = None,
        human_spot_check: HumanSpotCheckConfig | None = None,
        paraphraser: Callable[..., str] | None = None,
        rng_seed: int = 0,
        annotator_id: str = "panel-spot-check",
    ) -> None:
        if not name:
            raise ValueError("grader name must be non-empty")
        if not judges:
            raise ValueError("judge panel must have at least one judge")
        if consensus_rule not in _CONSENSUS_RULES:
            raise ValueError(
                f"consensus_rule must be one of {sorted(_CONSENSUS_RULES)}; got {consensus_rule!r}"
            )
        if weights is not None and len(weights) != len(judges):
            raise ValueError(
                f"weights length ({len(weights)}) must match judges length ({len(judges)})"
            )
        if weights is not None and any(w < 0 for w in weights):
            raise ValueError("judge weights must be non-negative")
        if consensus_rule == "weighted" and weights is None:
            raise ValueError("consensus_rule='weighted' requires weights")
        names = [j.name for j in judges]
        if len(set(names)) != len(names):
            raise ValueError(f"judge names must be unique within a panel; got {names}")

        self.name = name
        self._judges = list(judges)
        self._consensus_rule = consensus_rule
        self._weights = list(weights) if weights is not None else [1.0] * len(judges)
        self._counterfactuals = counterfactuals or CounterfactualConfig()
        self._human_spot_check = human_spot_check or HumanSpotCheckConfig()
        self._paraphraser = paraphraser or _default_paraphraser
        self._rng_seed = rng_seed
        self._annotator_id = annotator_id

    @classmethod
    def from_defenses(
        cls,
        name: str,
        *,
        judges: list[Grader],
        defenses: JudgeDefenses,
        weights: list[float] | None = None,
        paraphraser: Callable[..., str] | None = None,
        rng_seed: int = 0,
        annotator_id: str = "panel-spot-check",
    ) -> JudgePanelGrader:
        """Build a panel from a `JudgeDefenses` schema instance.

        Consumes `defenses.panel` (consensus rule), `defenses.counterfactuals`
        and `defenses.human_spot_check` — the schema fields that existed but
        had no runtime consumer. `defenses.panel.members` is treated as the
        intended membership; the caller still supplies the concrete `judges`
        (named graders), because the schema only carries member *names*.
        """

        panel = defenses.panel
        consensus_rule = panel.consensus_rule if panel is not None else "majority"
        cf = defenses.counterfactuals
        hsc = defenses.human_spot_check
        return cls(
            name,
            judges=judges,
            consensus_rule=consensus_rule,
            weights=weights,
            counterfactuals=CounterfactualConfig(
                enabled=cf.enabled,
                generation_strategy=cf.generation_strategy,
                pairs_per_case=cf.pairs_per_case,
                max_score_variance=cf.max_score_variance,
            ),
            human_spot_check=HumanSpotCheckConfig(
                enabled=hsc.enabled,
                sample_rate=hsc.sample_rate,
                trigger_on_jump=hsc.trigger_on_jump,
            ),
            paraphraser=paraphraser,
            rng_seed=rng_seed,
            annotator_id=annotator_id,
        )

    async def grade(self, context: GraderContext) -> GradeResult:
        outcomes = await self._run_panel(context)
        details: dict[str, Any] = {}

        voting = [o for o in outcomes if o.result.label in VOTING_LABELS]
        if not voting:
            # Every member errored or skipped: nothing to vote on.
            return GradeResult(
                grader=self.name,
                label=GradeLabel.ERROR,
                reason="all panel judges errored or were skipped; no verdict to combine",
                score=None,
                confidence=None,
                details=member_details(outcomes),
                breakdown=build_breakdown(outcomes, cf_node=None, consensus_rule=self._consensus_rule),
            )

        label = combine_labels(voting, consensus_rule=self._consensus_rule)
        score = combine_scores(voting, label, consensus_rule=self._consensus_rule)
        confidence = aggregate_confidence(voting)

        details.update(member_details(outcomes))

        cf_node: BreakdownNode | None = None
        if self._counterfactuals.enabled:
            cf_result = await self._counterfactual_variance(context, score)
            cf_node = cf_result.node
            details["counterfactual"] = cf_result.details
            if cf_result.high_variance and confidence is not None:
                # Advisory only: degrade confidence, never flip the label.
                confidence = confidence * 0.5

        if self._human_spot_check.enabled:
            details["human_spot_check"] = spot_check(
                context,
                label,
                rng_seed=self._rng_seed,
                sample_rate=self._human_spot_check.sample_rate,
                annotator_id=self._annotator_id,
            )

        calibration_result = calibration(context, label, confidence)
        if calibration_result is not None:
            details["calibration"] = calibration_result

        reason = build_reason(voting, label, self._consensus_rule)
        return GradeResult(
            grader=self.name,
            label=label,
            reason=reason,
            score=score,
            confidence=confidence,
            details=details,
            breakdown=build_breakdown(outcomes, cf_node=cf_node, consensus_rule=self._consensus_rule),
        )

    async def _run_panel(self, context: GraderContext) -> list[MemberOutcome]:
        results = await asyncio.gather(
            *(judge.grade(context) for judge in self._judges),
            return_exceptions=True,
        )
        outcomes: list[MemberOutcome] = []
        for judge, weight, raw in zip(self._judges, self._weights, results, strict=True):
            if isinstance(raw, BaseException):
                outcomes.append(
                    MemberOutcome(
                        name=judge.name,
                        result=GradeResult(
                            grader=judge.name,
                            label=GradeLabel.ERROR,
                            reason=f"judge raised: {raw}",
                        ),
                        weight=weight,
                    )
                )
            else:
                outcomes.append(MemberOutcome(name=judge.name, result=raw, weight=weight))
        return outcomes

    async def _counterfactual_variance(
        self, context: GraderContext, base_score: float
    ) -> _CounterfactualResult:
        n = self._counterfactuals.pairs_per_case
        variants = build_variant_contexts(context, n, paraphraser=self._paraphraser)
        scores = [base_score]
        trace_links: list[dict[str, str]] = []
        for variant_ctx in variants:
            outcomes = await self._run_panel(variant_ctx)
            voting = [o for o in outcomes if o.result.label in VOTING_LABELS]
            if not voting:
                continue
            variant_label = combine_labels(voting, consensus_rule=self._consensus_rule)
            scores.append(combine_scores(voting, variant_label, consensus_rule=self._consensus_rule))
            trace_links.append({"kind": "paraphrase_variant", "trace_id": variant_ctx.trace.id})

        variance = population_variance(scores)
        high = variance > self._counterfactuals.max_score_variance
        details = {
            "strategy": self._counterfactuals.generation_strategy,
            "pairs_per_case": n,
            "scores": scores,
            "variance": variance,
            "max_score_variance": self._counterfactuals.max_score_variance,
            "high_variance": high,
            "trace_links": trace_links,
        }
        node = BreakdownNode(
            key="counterfactual_variance",
            label=None,
            score=variance,
            weight=0.0,  # advisory: never contributes to the verdict
            reason=(
                f"score variance {variance:.4f} over {len(scores)} paraphrase variants "
                f"(threshold {self._counterfactuals.max_score_variance})"
            ),
            failure_modes=["judge_instability"] if high else [],
        )
        return _CounterfactualResult(node=node, details=details, high_variance=high)


__all__ = [
    "CounterfactualConfig",
    "HumanSpotCheckConfig",
    "JudgePanelGrader",
]
