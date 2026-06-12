"""Pure confusion-matrix math over arbitrary string classes.

The NxN confusion matrix + per-class precision/recall/F1 + macro-F1 is the same
arithmetic whether the classes are the `GradeLabel` enum (calibration:
prediction-vs-human) or arbitrary domain classes (`full_order` / `special_order`
/ `refund`, the `ClassificationGrader`'s `type: confusion`). Keeping the formula
in one place means there is exactly one definition of F1 in the codebase.

A class-imbalance guard carried over from calibration: if a class appears only
as an actual or only as a predicted label, its precision/recall is reported as
`None` rather than a misleading zero (the "100% precision on a class with 0
predictions" trap). `None` per-class F1 is excluded from the macro average.

`confusion_from_pairs` takes `(expected, predicted)` pairs — expected first,
matching the row=expected / column=predicted convention the reporter renders.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any


def _safe_div(num: float, den: float) -> float | None:
    if den == 0:
        return None
    return num / den


def f1_score(precision: float | None, recall: float | None) -> float | None:
    """Harmonic mean of precision and recall, or `None` when either is undefined.

    The single F1 definition in the codebase — both the calibration report and
    the classification rollup route through here so the formula is never
    duplicated.
    """
    if precision is None or recall is None or (precision + recall) == 0:
        return None
    return 2 * precision * recall / (precision + recall)


@dataclass(frozen=True)
class ConfusionReport:
    """NxN confusion matrix + per-class P/R/F1 + macro-F1 over string classes.

    - `labels`: the sorted union of every class observed as expected or predicted.
    - `confusion`: `(expected, predicted) -> count`. The diagonal is correct.
    - `per_label_precision` / `per_label_recall`: per-class, `None` when the
      class has no predicted (resp. actual) instances (imbalance guard).
    - `per_label_f1`: harmonic mean per class, `None` when P or R is `None`.
    - `macro_f1`: mean of the non-`None` per-class F1s, `None` when none exist.
    - `accuracy`: correct (diagonal) over total pairs.
    - `n_pairs`: total `(expected, predicted)` pairs scored.
    """

    n_pairs: int
    accuracy: float
    macro_f1: float | None
    labels: list[str] = field(default_factory=list)
    confusion: dict[tuple[str, str], int] = field(default_factory=dict)
    per_label_precision: dict[str, float | None] = field(default_factory=dict)
    per_label_recall: dict[str, float | None] = field(default_factory=dict)
    per_label_f1: dict[str, float | None] = field(default_factory=dict)

    def to_nested(self) -> dict[str, dict[str, int]]:
        """JSON-serializable `{expected: {predicted: count}}` view of the matrix.

        `tuple[str, str]` keys are not JSON keys, so persisting the matrix means
        nesting it. Only observed cells are present; absent cells are 0.
        """
        nested: dict[str, dict[str, int]] = {}
        for (expected, predicted), count in self.confusion.items():
            nested.setdefault(expected, {})[predicted] = count
        return nested

    def to_dict(self) -> dict[str, Any]:
        """Fully JSON-serializable view (the form persisted on IterationMetrics)."""
        return {
            "n_pairs": self.n_pairs,
            "accuracy": self.accuracy,
            "macro_f1": self.macro_f1,
            "labels": list(self.labels),
            "matrix": self.to_nested(),
            "per_label_precision": dict(self.per_label_precision),
            "per_label_recall": dict(self.per_label_recall),
            "per_label_f1": dict(self.per_label_f1),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConfusionReport:
        """Rebuild a report from its `to_dict` form (round-trips from storage)."""
        matrix = data.get("matrix", {})
        confusion: dict[tuple[str, str], int] = {}
        for expected, row in matrix.items():
            for predicted, count in row.items():
                confusion[(str(expected), str(predicted))] = int(count)
        return cls(
            n_pairs=int(data.get("n_pairs", 0)),
            accuracy=float(data.get("accuracy", 0.0)),
            macro_f1=data.get("macro_f1"),
            labels=[str(x) for x in data.get("labels", [])],
            confusion=confusion,
            per_label_precision=dict(data.get("per_label_precision", {})),
            per_label_recall=dict(data.get("per_label_recall", {})),
            per_label_f1=dict(data.get("per_label_f1", {})),
        )


def confusion_from_pairs(pairs: Iterable[tuple[str, str]]) -> ConfusionReport:
    """Build a `ConfusionReport` from `(expected, predicted)` class pairs.

    The pure core both `compute_classification_metrics` (calibration) and the
    `ClassificationGrader` rollup route through. Expected is the first element of
    each pair (row), predicted the second (column).
    """
    confusion: Counter[tuple[str, str]] = Counter()
    correct = 0
    n = 0
    for expected, predicted in pairs:
        confusion[(expected, predicted)] += 1
        n += 1
        if expected == predicted:
            correct += 1

    if n == 0:
        return ConfusionReport(n_pairs=0, accuracy=0.0, macro_f1=None)

    labels = sorted({k[0] for k in confusion} | {k[1] for k in confusion})
    per_label_precision: dict[str, float | None] = {}
    per_label_recall: dict[str, float | None] = {}
    per_label_f1: dict[str, float | None] = {}
    f1_values: list[float] = []
    for label in labels:
        tp = confusion[(label, label)]
        predicted_pos = sum(c for (exp, pred), c in confusion.items() if pred == label)
        actual_pos = sum(c for (exp, pred), c in confusion.items() if exp == label)
        precision = _safe_div(tp, predicted_pos)
        recall = _safe_div(tp, actual_pos)
        per_label_precision[label] = precision
        per_label_recall[label] = recall
        f1 = f1_score(precision, recall)
        per_label_f1[label] = f1
        if f1 is not None:
            f1_values.append(f1)

    macro_f1 = sum(f1_values) / len(f1_values) if f1_values else None
    return ConfusionReport(
        n_pairs=n,
        accuracy=correct / n,
        macro_f1=macro_f1,
        labels=labels,
        confusion=dict(confusion),
        per_label_precision=per_label_precision,
        per_label_recall=per_label_recall,
        per_label_f1=per_label_f1,
    )
