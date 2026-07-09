"""Free functions for `JudgePanelGrader`'s consensus/breakdown/spot-check logic.

Split out of `judge_panel.py` (same free-function-delegate pattern used
across the codebase's other oversized classes): each function is pure given
its inputs — the panel's config (consensus rule, weights, rng seed, ...) is
passed as a parameter instead of read off `self`. `JudgePanelGrader` stays
the single `Grader` with all of this wired together, just thinner.
"""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from selfevals.graders.base import BreakdownNode, GradeLabel, GraderContext, GradeResult
from selfevals.graders.calibration import HumanLabel, PredictedLabel, compute_classification_metrics

if TYPE_CHECKING:
    from selfevals.graders.judge_panel import ConsensusRule

# Numeric mapping used when a member judge supplies no explicit score, and
# when scoring counterfactual variance. Conservative ordering: a lower score
# is a more conservative (closer to fail) verdict.
_LABEL_SCORE: dict[GradeLabel, float] = {
    GradeLabel.PASS: 1.0,
    GradeLabel.PARTIAL: 0.5,
    GradeLabel.FAIL: 0.0,
}

# Verdict labels eligible to win a vote (ERROR/SKIPPED are excluded).
VOTING_LABELS = (GradeLabel.FAIL, GradeLabel.PARTIAL, GradeLabel.PASS)

# Conservatism ranking for tie-breaking: a single fail should dominate.
_CONSERVATISM_RANK: dict[GradeLabel, int] = {
    GradeLabel.FAIL: 0,
    GradeLabel.PARTIAL: 1,
    GradeLabel.PASS: 2,
}


@dataclass(frozen=True)
class MemberOutcome:
    name: str
    result: GradeResult
    weight: float


def argmax_label(tally: Mapping[GradeLabel, float]) -> GradeLabel:
    # Highest tally wins; ties broken toward the more conservative label.
    return max(
        (lbl for lbl in VOTING_LABELS if tally.get(lbl, 0)),
        key=lambda lbl: (tally.get(lbl, 0.0), -_CONSERVATISM_RANK[lbl]),
    )


def combine_labels(voting: list[MemberOutcome], *, consensus_rule: ConsensusRule) -> GradeLabel:
    if consensus_rule == "unanimous":
        labels = {o.result.label for o in voting}
        if labels == {GradeLabel.PASS}:
            return GradeLabel.PASS
        # Not unanimous pass: the most conservative observed label wins.
        return min((o.result.label for o in voting), key=lambda lbl: _CONSERVATISM_RANK[lbl])
    if consensus_rule == "weighted":
        weighted_tally: dict[GradeLabel, float] = {}
        for o in voting:
            weighted_tally[o.result.label] = weighted_tally.get(o.result.label, 0.0) + o.weight
        return argmax_label(weighted_tally)
    # majority (default): count of votes, ties resolved conservatively.
    counts: Counter[GradeLabel] = Counter(o.result.label for o in voting)
    return argmax_label(counts)


def member_score(result: GradeResult) -> float:
    if result.score is not None:
        return result.score
    return _LABEL_SCORE.get(result.label, 0.0)


def combine_scores(
    voting: list[MemberOutcome], label: GradeLabel, *, consensus_rule: ConsensusRule
) -> float:
    # Weighted mean of member scores (explicit score if given, else the
    # label-implied score). For weighted consensus the member weights are
    # used; otherwise equal weights.
    use_weights = consensus_rule == "weighted"
    num = 0.0
    den = 0.0
    for o in voting:
        w = o.weight if use_weights else 1.0
        num += w * member_score(o.result)
        den += w
    if den == 0:
        return _LABEL_SCORE[label]
    return num / den


def aggregate_confidence(voting: list[MemberOutcome]) -> float | None:
    confidences = [o.result.confidence for o in voting if o.result.confidence is not None]
    if not confidences:
        return None
    return sum(confidences) / len(confidences)


def population_variance(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return sum((v - mean) ** 2 for v in values) / len(values)


def build_variant_contexts(
    context: GraderContext, n: int, *, paraphraser: Any
) -> list[GraderContext]:
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
        new_text = paraphraser(base_text, variant_index=i)
        new_response = (
            replace(context.response, content=new_text)
            if context.response is not None
            else AdapterResponse(content=new_text)
        )
        variant_trace = context.trace.model_copy(update={"id": f"{context.trace.id}-cf{i}"})
        variants.append(
            GraderContext(case=context.case, trace=variant_trace, response=new_response)
        )
    return variants


def spot_check(
    context: GraderContext,
    label: GradeLabel,
    *,
    rng_seed: int,
    sample_rate: float,
    annotator_id: str,
) -> dict[str, Any]:
    # Seeded sampler: deterministic given (rng_seed, case id). Emits an
    # Annotation stub when selected; never blocks the verdict.
    rng = random.Random(f"{rng_seed}:{context.case.id}")
    roll = rng.random()
    selected = roll < sample_rate
    out: dict[str, Any] = {"selected": selected, "sample_rate": sample_rate, "roll": roll}
    if selected:
        out["annotation_stub"] = _annotation_stub(context, label, annotator_id=annotator_id)
    return out


def _annotation_stub(
    context: GraderContext, label: GradeLabel, *, annotator_id: str
) -> dict[str, Any]:
    from selfevals.schemas.annotation import Annotation, AnnotationLabels

    stub = Annotation(
        id=Annotation.make_id(),
        workspace_id=context.case.workspace_id,
        case_id=context.case.id,
        trace_id=context.trace.id,
        annotator_id=annotator_id,
        labels=AnnotationLabels(data={"panel_label": label.value, "status": "pending_human_review"}),
        notes="seeded panel spot-check; awaiting human label",
        flagged_for_adjudication=True,
    )
    return stub.model_dump(mode="json")


def reachable_human_label(context: GraderContext) -> GradeLabel | None:
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


def calibration(
    context: GraderContext, label: GradeLabel, confidence: float | None
) -> dict[str, Any] | None:
    human_label = reachable_human_label(context)
    if human_label is None:
        return None
    report = compute_classification_metrics(
        predictions=[PredictedLabel(case_id=context.case.id, label=label, confidence=confidence)],
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


def build_breakdown(
    outcomes: list[MemberOutcome], *, cf_node: BreakdownNode | None, consensus_rule: ConsensusRule
) -> BreakdownNode:
    children: list[BreakdownNode] = [
        BreakdownNode(
            key=o.name,
            label=o.result.label,
            score=o.result.score,
            weight=o.weight,
            reason=o.result.reason,
            failure_modes=list(o.result.failure_modes),
        )
        for o in outcomes
    ]
    if cf_node is not None:
        children.append(cf_node)
    return BreakdownNode(
        key="judge_panel", reason=f"consensus_rule={consensus_rule}", children=children
    )


def member_details(outcomes: list[MemberOutcome]) -> dict[str, Any]:
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


def build_reason(voting: list[MemberOutcome], label: GradeLabel, rule: ConsensusRule) -> str:
    breakdown = ", ".join(f"{o.name}={o.result.label.value}" for o in voting)
    return f"panel verdict {label.value} via {rule} consensus ({breakdown})"
