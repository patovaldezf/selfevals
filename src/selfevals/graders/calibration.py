"""Calibration helpers: turn predictions + human labels into classification metrics.

These functions live outside any specific grader so they can be reused by
calibration scripts, dashboards, and the optimizer. They consume two flat
lists keyed by `case_id` and produce a `CalibrationReport`.

Metrics are computed treating `pass` as the positive class by default;
`positive_label` is configurable. Macro-F1 is averaged across all observed
labels.

A class-imbalance guard: if a label appears in only one of the two streams,
precision/recall for that class are reported as `None` rather than zero —
this avoids the "100% precision on a class with 0 predictions" trap.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field

from selfevals.graders.base import GradeLabel


@dataclass(frozen=True)
class PredictedLabel:
    case_id: str
    label: GradeLabel
    confidence: float | None = None


@dataclass(frozen=True)
class HumanLabel:
    case_id: str
    label: GradeLabel
    high_risk: bool = False


@dataclass(frozen=True)
class CalibrationReport:
    n_pairs: int
    precision: float | None
    recall: float | None
    f1: float | None
    macro_f1: float | None
    accuracy: float
    high_risk_false_negatives: int
    per_label_precision: dict[str, float | None] = field(default_factory=dict)
    per_label_recall: dict[str, float | None] = field(default_factory=dict)
    confusion: dict[tuple[str, str], int] = field(default_factory=dict)


def _safe_div(num: float, den: float) -> float | None:
    if den == 0:
        return None
    return num / den


def _f1(p: float | None, r: float | None) -> float | None:
    if p is None or r is None or (p + r) == 0:
        return None
    return 2 * p * r / (p + r)


def compute_classification_metrics(
    predictions: list[PredictedLabel],
    human_labels: list[HumanLabel],
    *,
    positive_label: GradeLabel = GradeLabel.PASS,
) -> CalibrationReport:
    """Compute precision/recall/F1/macro-F1 + high-risk FNs.

    Pairs are joined on case_id. Cases with a prediction but no human label
    (or vice versa) are dropped from the metrics but counted via n_pairs=0
    if there are no pairs at all.
    """
    pred_by_case: Mapping[str, PredictedLabel] = {p.case_id: p for p in predictions}
    human_by_case: Mapping[str, HumanLabel] = {h.case_id: h for h in human_labels}
    paired_ids = sorted(set(pred_by_case) & set(human_by_case))
    n = len(paired_ids)

    if n == 0:
        return CalibrationReport(
            n_pairs=0,
            precision=None,
            recall=None,
            f1=None,
            macro_f1=None,
            accuracy=0.0,
            high_risk_false_negatives=0,
        )

    confusion: Counter[tuple[str, str]] = Counter()
    correct = 0
    high_risk_fns = 0
    for case_id in paired_ids:
        pred = pred_by_case[case_id].label.value
        human = human_by_case[case_id].label.value
        confusion[(human, pred)] += 1
        if pred == human:
            correct += 1
        if (
            human == positive_label.value
            and pred != positive_label.value
            and human_by_case[case_id].high_risk
        ):
            high_risk_fns += 1

    accuracy = correct / n

    labels = sorted({k[0] for k in confusion} | {k[1] for k in confusion})
    per_label_precision: dict[str, float | None] = {}
    per_label_recall: dict[str, float | None] = {}
    f1_values: list[float] = []
    for label in labels:
        tp = confusion[(label, label)]
        pred_pos = sum(c for (h, p), c in confusion.items() if p == label)
        actual_pos = sum(c for (h, p), c in confusion.items() if h == label)
        precision_l = _safe_div(tp, pred_pos)
        recall_l = _safe_div(tp, actual_pos)
        per_label_precision[label] = precision_l
        per_label_recall[label] = recall_l
        f1_l = _f1(precision_l, recall_l)
        if f1_l is not None:
            f1_values.append(f1_l)

    macro_f1 = sum(f1_values) / len(f1_values) if f1_values else None

    pos = positive_label.value
    precision_pos = per_label_precision.get(pos)
    recall_pos = per_label_recall.get(pos)
    f1_pos = _f1(precision_pos, recall_pos)

    return CalibrationReport(
        n_pairs=n,
        precision=precision_pos,
        recall=recall_pos,
        f1=f1_pos,
        macro_f1=macro_f1,
        accuracy=accuracy,
        high_risk_false_negatives=high_risk_fns,
        per_label_precision=per_label_precision,
        per_label_recall=per_label_recall,
        confusion=dict(confusion),
    )
