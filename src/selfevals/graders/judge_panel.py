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
"""

from __future__ import annotations

import asyncio
import random
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from selfevals.graders.base import (
    BreakdownNode,
    GradeLabel,
    Grader,
    GraderContext,
    GradeResult,
)
from selfevals.graders.calibration import (
    HumanLabel,
    PredictedLabel,
    compute_classification_metrics,
)

if TYPE_CHECKING:
    from selfevals.schemas.experiment import JudgeDefenses

# Numeric mapping used when a member judge supplies no explicit score, and
# when scoring counterfactual variance. Conservative ordering: a lower score
# is a more conservative (closer to fail) verdict.
_LABEL_SCORE: dict[GradeLabel, float] = {
    GradeLabel.PASS: 1.0,
    GradeLabel.PARTIAL: 0.5,
    GradeLabel.FAIL: 0.0,
}

# Verdict labels eligible to win a vote (ERROR/SKIPPED are excluded).
_VOTING_LABELS = (GradeLabel.FAIL, GradeLabel.PARTIAL, GradeLabel.PASS)

# Conservatism ranking for tie-breaking: a single fail should dominate.
_CONSERVATISM_RANK: dict[GradeLabel, int] = {
    GradeLabel.FAIL: 0,
    GradeLabel.PARTIAL: 1,
    GradeLabel.PASS: 2,
}

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
class _MemberOutcome:
    name: str
    result: GradeResult
    weight: float


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

        voting = [o for o in outcomes if o.result.label in _VOTING_LABELS]
        if not voting:
            # Every member errored or skipped: nothing to vote on.
            return GradeResult(
                grader=self.name,
                label=GradeLabel.ERROR,
                reason="all panel judges errored or were skipped; no verdict to combine",
                score=None,
                confidence=None,
                details=self._member_details(outcomes),
                breakdown=self._build_breakdown(outcomes, cf_node=None),
            )

        label = self._combine_labels(voting)
        score = self._combine_scores(voting, label)
        confidence = self._aggregate_confidence(voting)

        details.update(self._member_details(outcomes))

        cf_node: BreakdownNode | None = None
        if self._counterfactuals.enabled:
            cf_result = await self._counterfactual_variance(context, score)
            cf_node = cf_result.node
            details["counterfactual"] = cf_result.details
            if cf_result.high_variance and confidence is not None:
                # Advisory only: degrade confidence, never flip the label.
                confidence = confidence * 0.5

        if self._human_spot_check.enabled:
            details["human_spot_check"] = self._spot_check(context, label)

        calibration = self._calibration(context, label, confidence)
        if calibration is not None:
            details["calibration"] = calibration

        reason = self._build_reason(voting, label, self._consensus_rule)
        return GradeResult(
            grader=self.name,
            label=label,
            reason=reason,
            score=score,
            confidence=confidence,
            details=details,
            breakdown=self._build_breakdown(outcomes, cf_node=cf_node),
        )

    # -- panel execution -------------------------------------------------

    async def _run_panel(self, context: GraderContext) -> list[_MemberOutcome]:
        results = await asyncio.gather(
            *(judge.grade(context) for judge in self._judges),
            return_exceptions=True,
        )
        outcomes: list[_MemberOutcome] = []
        for judge, weight, raw in zip(self._judges, self._weights, results, strict=True):
            if isinstance(raw, BaseException):
                outcomes.append(
                    _MemberOutcome(
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
                outcomes.append(_MemberOutcome(name=judge.name, result=raw, weight=weight))
        return outcomes

    # -- consensus -------------------------------------------------------

    def _combine_labels(self, voting: list[_MemberOutcome]) -> GradeLabel:
        rule = self._consensus_rule
        if rule == "unanimous":
            labels = {o.result.label for o in voting}
            if labels == {GradeLabel.PASS}:
                return GradeLabel.PASS
            # Not unanimous pass: the most conservative observed label wins.
            return min(
                (o.result.label for o in voting),
                key=lambda lbl: _CONSERVATISM_RANK[lbl],
            )
        if rule == "weighted":
            weighted_tally: dict[GradeLabel, float] = {}
            for o in voting:
                weighted_tally[o.result.label] = weighted_tally.get(o.result.label, 0.0) + o.weight
            return self._argmax_label(weighted_tally)
        # majority (default): count of votes, ties resolved conservatively.
        counts: Counter[GradeLabel] = Counter(o.result.label for o in voting)
        return self._argmax_label(counts)

    @staticmethod
    def _argmax_label(tally: Mapping[GradeLabel, float]) -> GradeLabel:
        # Highest tally wins; ties broken toward the more conservative label.
        return max(
            (lbl for lbl in _VOTING_LABELS if tally.get(lbl, 0)),
            key=lambda lbl: (tally.get(lbl, 0.0), -_CONSERVATISM_RANK[lbl]),
        )

    def _combine_scores(self, voting: list[_MemberOutcome], label: GradeLabel) -> float:
        # Weighted mean of member scores (explicit score if given, else the
        # label-implied score). For weighted consensus the member weights are
        # used; otherwise equal weights.
        use_weights = self._consensus_rule == "weighted"
        num = 0.0
        den = 0.0
        for o in voting:
            w = o.weight if use_weights else 1.0
            num += w * self._member_score(o.result)
            den += w
        if den == 0:
            return _LABEL_SCORE[label]
        return num / den

    @staticmethod
    def _member_score(result: GradeResult) -> float:
        if result.score is not None:
            return result.score
        return _LABEL_SCORE.get(result.label, 0.0)

    @staticmethod
    def _aggregate_confidence(voting: list[_MemberOutcome]) -> float | None:
        confidences = [o.result.confidence for o in voting if o.result.confidence is not None]
        if not confidences:
            return None
        return sum(confidences) / len(confidences)

    # -- counterfactual variance ----------------------------------------

    @dataclass(frozen=True)
    class _CounterfactualResult:
        node: BreakdownNode
        details: dict[str, Any]
        high_variance: bool

    async def _counterfactual_variance(
        self, context: GraderContext, base_score: float
    ) -> _CounterfactualResult:
        n = self._counterfactuals.pairs_per_case
        variants = self._build_variant_contexts(context, n)
        scores = [base_score]
        trace_links: list[dict[str, str]] = []
        for variant_ctx in variants:
            outcomes = await self._run_panel(variant_ctx)
            voting = [o for o in outcomes if o.result.label in _VOTING_LABELS]
            if not voting:
                continue
            variant_label = self._combine_labels(voting)
            scores.append(self._combine_scores(voting, variant_label))
            trace_links.append({"kind": "paraphrase_variant", "trace_id": variant_ctx.trace.id})

        variance = _population_variance(scores)
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
        return self._CounterfactualResult(node=node, details=details, high_variance=high)

    def _build_variant_contexts(self, context: GraderContext, n: int) -> list[GraderContext]:
        """Generate `n` paraphrased copies of the context.

        Paraphrasing targets the response text the judge reads (the agent
        output under evaluation). Each variant gets a distinct synthetic
        trace id so it can be linked via `TraceLink(kind="paraphrase_variant")`.
        """

        from selfevals.runner.adapters import AdapterResponse  # local: avoid cycle

        base_text = context.response.content if context.response is not None else ""
        base_text = base_text or ""
        variants: list[GraderContext] = []
        for i in range(n):
            new_text = self._paraphraser(base_text, variant_index=i)
            new_response = (
                replace(context.response, content=new_text)
                if context.response is not None
                else AdapterResponse(content=new_text)
            )
            variant_trace = context.trace.model_copy(update={"id": f"{context.trace.id}-cf{i}"})
            variants.append(
                GraderContext(
                    case=context.case,
                    trace=variant_trace,
                    response=new_response,
                )
            )
        return variants

    # -- human spot-check -----------------------------------------------

    def _spot_check(self, context: GraderContext, label: GradeLabel) -> dict[str, Any]:
        # Seeded sampler: deterministic given (rng_seed, case id). Emits an
        # Annotation stub when selected; never blocks the verdict.
        rng = random.Random(f"{self._rng_seed}:{context.case.id}")
        roll = rng.random()
        selected = roll < self._human_spot_check.sample_rate
        out: dict[str, Any] = {
            "selected": selected,
            "sample_rate": self._human_spot_check.sample_rate,
            "roll": roll,
        }
        if selected:
            out["annotation_stub"] = self._annotation_stub(context, label)
        return out

    def _annotation_stub(self, context: GraderContext, label: GradeLabel) -> dict[str, Any]:
        from selfevals.schemas.annotation import Annotation, AnnotationLabels

        stub = Annotation(
            id=Annotation.make_id(),
            workspace_id=context.case.workspace_id,
            case_id=context.case.id,
            trace_id=context.trace.id,
            annotator_id=self._annotator_id,
            labels=AnnotationLabels(
                data={"panel_label": label.value, "status": "pending_human_review"}
            ),
            notes="seeded panel spot-check; awaiting human label",
            flagged_for_adjudication=True,
        )
        return stub.model_dump(mode="json")

    # -- calibration -----------------------------------------------------

    def _calibration(
        self, context: GraderContext, label: GradeLabel, confidence: float | None
    ) -> dict[str, Any] | None:
        human_label = _reachable_human_label(context)
        if human_label is None:
            return None
        report = compute_classification_metrics(
            predictions=[
                PredictedLabel(case_id=context.case.id, label=label, confidence=confidence)
            ],
            human_labels=[HumanLabel(case_id=context.case.id, label=human_label)],
        )
        return {
            "advisory": True,
            "human_label": human_label.value,
            "panel_label": label.value,
            "n_pairs": report.n_pairs,
            "accuracy": report.accuracy,
            "agreement": label == human_label,
        }

    # -- breakdown + details --------------------------------------------

    def _build_breakdown(
        self, outcomes: list[_MemberOutcome], *, cf_node: BreakdownNode | None
    ) -> BreakdownNode:
        children: list[BreakdownNode] = []
        for o in outcomes:
            children.append(
                BreakdownNode(
                    key=o.name,
                    label=o.result.label,
                    score=o.result.score,
                    weight=o.weight,
                    reason=o.result.reason,
                    failure_modes=list(o.result.failure_modes),
                )
            )
        if cf_node is not None:
            children.append(cf_node)
        return BreakdownNode(
            key="judge_panel",
            reason=f"consensus_rule={self._consensus_rule}",
            children=children,
        )

    @staticmethod
    def _member_details(outcomes: list[_MemberOutcome]) -> dict[str, Any]:
        return {
            "judges": [
                {
                    "name": o.name,
                    "label": o.result.label.value,
                    "score": o.result.score,
                    "confidence": o.result.confidence,
                    "weight": o.weight,
                    "reason": o.result.reason,
                }
                for o in outcomes
            ]
        }

    @staticmethod
    def _build_reason(voting: list[_MemberOutcome], label: GradeLabel, rule: ConsensusRule) -> str:
        breakdown = ", ".join(f"{o.name}={o.result.label.value}" for o in voting)
        return f"panel verdict {label.value} via {rule} consensus ({breakdown})"


def _population_variance(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return sum((v - mean) ** 2 for v in values) / len(values)


def _reachable_human_label(context: GraderContext) -> GradeLabel | None:
    """Pull a human label off `case.context` if one is encoded there.

    Looks for `case.context["human_label"]` as a `GradeLabel` value string.
    Returns None when absent or unparseable — calibration stays advisory and
    silent rather than erroring.
    """

    ctx = context.case.context
    if not ctx:
        return None
    raw = ctx.get("human_label")
    if raw is None:
        return None
    try:
        return GradeLabel(str(raw).strip().lower())
    except ValueError:
        return None


__all__ = [
    "CounterfactualConfig",
    "HumanSpotCheckConfig",
    "JudgePanelGrader",
]
