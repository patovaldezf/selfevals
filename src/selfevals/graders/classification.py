"""ClassificationGrader: single-label N-class classification (`type: confusion`).

The capability a confusion matrix exists to score: the agent emits **one class**
per case (`full_order` / `special_order` / `refund`, an intent, a routing label)
and the eval asks not just "how often is it right" but "*which* class gets
mistaken for *which*". Exact-match collapses that to a flat pass-rate; a
confusion matrix keeps the directed error structure (refund→full_order is a
different bug than full_order→refund).

This grader is the per-case half. It:

1. extracts the **predicted** class from `structured_output` via a path selector
   (`extract`, default ``"label"``), and
2. reads the **expected** class from the case (`Expected.outcome` by default, or
   a path into `Expected.structured_output` when `expected_from` is set),

then labels PASS iff they match.

**How the `(expected, predicted)` pair survives to the aggregator.** A grade's
`details` are dropped by `aggregator._outcome_for` — only `failure_modes` and the
`breakdown` tree are kept. So the grader encodes the pair into the **breakdown
node key**: ``cell:<expected>-><predicted>``. The funnel rollup
(`aggregator._rollup_funnel`) groups breakdown nodes by `key` and counts them, so
every distinct cell becomes a `FunnelNode` whose `count` is that cell's tally —
the NxN matrix falls straight out of the existing rollup machinery, no new
`CaseOutcome` channel needed, and the **diagonal (correct) cells survive** because
the breakdown is emitted on PASS too (the failure-mode tag only fires on a miss).
The aggregator parses these keys back into pairs and feeds `confusion_from_pairs`.

On a mismatch the grader *also* emits the failure mode
``misclassified:<predicted>-><expected>`` — the same tag the DeterministicGrader's
class-label branch emits (SF-1) — so the human-readable error signal and the
`failuremode` analysis keep working. That tag is redundant with the breakdown
cell for the matrix, but it is the off-diagonal signal a reader scans first.

selfevals stays the scoring authority and domain-agnostic: which key holds the
class and any alias normalization live in the *case*, never hard-coded here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from selfevals.graders._select import select, validate_path
from selfevals.graders.base import (
    BreakdownNode,
    GradeLabel,
    Grader,
    GraderContext,
    GradeResult,
)

if TYPE_CHECKING:
    from selfevals.schemas.eval_case import Expected

FM_NO_PREDICTION = "classification_no_prediction"
"""The response carried no usable class at the `extract` path."""
FM_NO_EXPECTED = "classification_no_expected"
"""The case declared no expected class to compare against."""

MISCLASSIFIED_PREFIX = "misclassified:"
"""Failure-mode prefix shared with `deterministic._class_label` (SF-1)."""

CELL_PREFIX = "cell:"
"""Breakdown-key prefix encoding one confusion cell as
``cell:<expected>-><predicted>``. The aggregator's funnel rollup counts nodes by
key, so each cell's `count` is its tally; `parse_cell_key` reverses it."""


def misclassified_mode(predicted: str, expected: str) -> str:
    """The shared `misclassified:<predicted>-><expected>` failure-mode tag."""
    return f"{MISCLASSIFIED_PREFIX}{predicted}->{expected}"


def cell_key(expected: str, predicted: str) -> str:
    """The breakdown key for the `(expected, predicted)` confusion cell."""
    return f"{CELL_PREFIX}{expected}->{predicted}"


def parse_cell_key(key: str) -> tuple[str, str] | None:
    """Inverse of `cell_key`: `(expected, predicted)` or `None`.

    Returns the pair in `(expected, predicted)` order (the confusion row/column
    convention) so the aggregator feeds `confusion_from_pairs` directly. A key
    that is not a well-formed ``cell:e->p`` yields `None`.
    """
    if not key.startswith(CELL_PREFIX):
        return None
    body = key[len(CELL_PREFIX) :]
    expected, sep, predicted = body.partition("->")
    if not sep or not expected or not predicted:
        return None
    return (expected, predicted)


def _as_class_label(value: object) -> str | None:
    """Coerce a selected value to a class label string, or `None`.

    Accepts a bare scalar (`str`/`int`/`bool`) — the shape a single-label
    classifier produces. A list/dict/None is not a single class, so it yields
    `None` (the "no usable prediction" signal). Mirrors
    `deterministic._class_label`'s scalar acceptance without the single-key-dict
    unwrap (the path selector already descends into dicts).
    """
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, str | int):
        return str(value)
    return None


def _extract_predicted(context: GraderContext, extract: str) -> str | None:
    response = context.response
    if response is None or response.structured_output is None:
        return None
    return _as_class_label(select(response.structured_output, extract))


def _extract_expected(expected_spec: Expected, expected_from: str | None) -> str | None:
    """Read the expected class from the case.

    Default: `Expected.outcome` (the case's declared single outcome). When
    `expected_from` is set, select that path into `Expected.structured_output`
    instead — for cases that carry the ground-truth class inside a structured
    contract rather than the scalar `outcome`.
    """
    if expected_from is not None:
        if expected_spec.structured_output is None:
            return None
        return _as_class_label(select(expected_spec.structured_output, expected_from))
    return _as_class_label(expected_spec.outcome)


class ClassificationGrader(Grader):
    """Score a single predicted class against the case's expected class.

    No-arg constructible (registry- and dotted-path-friendly). `extract` is the
    path selector into `structured_output` for the predicted class (default
    ``"label"``); `expected_from`, when set, is the path into
    `Expected.structured_output` for the ground-truth class (default: read
    `Expected.outcome`). `case_sensitive` controls whether the class comparison
    folds case (default: case-insensitive, matching `set_match`).
    """

    def __init__(
        self,
        name: str = "classification",
        *,
        extract: str = "label",
        expected_from: str | None = None,
        case_sensitive: bool = False,
    ) -> None:
        if not name:
            raise ValueError("grader name must be non-empty")
        validate_path(extract)
        if expected_from is not None:
            validate_path(expected_from)
        self.name = name
        self._extract = extract
        self._expected_from = expected_from
        self._case_sensitive = case_sensitive

    def _fold(self, label: str) -> str:
        return label if self._case_sensitive else label.lower()

    async def grade(self, context: GraderContext) -> GradeResult:
        expected_spec: Expected = context.case.expected

        raw_predicted = _extract_predicted(context, self._extract)
        if raw_predicted is None:
            return GradeResult(
                grader=self.name,
                label=GradeLabel.FAIL,
                reason=f"no usable predicted class at structured_output path {self._extract!r}",
                score=0.0,
                failure_modes=[FM_NO_PREDICTION],
                details={"extract": self._extract},
            )

        raw_expected = _extract_expected(expected_spec, self._expected_from)
        if raw_expected is None:
            return GradeResult(
                grader=self.name,
                label=GradeLabel.FAIL,
                reason="case declares no expected class to compare against",
                score=0.0,
                failure_modes=[FM_NO_EXPECTED],
                details={"predicted": raw_predicted, "extract": self._extract},
            )

        predicted = self._fold(raw_predicted)
        expected = self._fold(raw_expected)
        matched = predicted == expected
        label = GradeLabel.PASS if matched else GradeLabel.FAIL

        failure_modes = [] if matched else [misclassified_mode(predicted, expected)]
        details: dict[str, Any] = {
            "predicted": predicted,
            "expected": expected,
            "matched": matched,
            "extract": self._extract,
        }
        reason = (
            f"predicted {predicted!r} == expected {expected!r}"
            if matched
            else f"predicted {predicted!r} != expected {expected!r}"
        )
        return GradeResult(
            grader=self.name,
            label=label,
            reason=reason,
            score=1.0 if matched else 0.0,
            failure_modes=failure_modes,
            details=details,
            breakdown=_build_breakdown(
                label=label, predicted=predicted, expected=expected, matched=matched
            ),
        )


def _build_breakdown(
    *, label: GradeLabel, predicted: str, expected: str, matched: bool
) -> BreakdownNode:
    """Authoritative root over a single `cell:<expected>-><predicted>` child.

    The cell lives on a CHILD node, not the root, on purpose: the multi-turn
    collapse (`loop._collapse_multiturn`) discards a grade's breakdown *root* and
    grafts only its *children* under the per-turn node. A cell carried on the
    root would vanish for conversation cases; carried on a child it survives the
    graft. The aggregator's `_rollup_confusion` walks the tree recursively and
    counts every `cell:` node it finds, so the cell's tally reaches the matrix
    whether the case was single-turn (cell nested under `classification`) or
    multi-turn (cell grafted under `turn_N`). Emitted on PASS too, so the
    diagonal (correct) cells survive — the failure-mode tag alone would only
    record off-diagonal misses.
    """
    cell = BreakdownNode(
        key=cell_key(expected, predicted),
        label=label,
        score=1.0 if matched else 0.0,
        weight=1.0,
        reason=f"expected {expected!r} -> predicted {predicted!r}",
        failure_modes=[] if matched else [misclassified_mode(predicted, expected)],
    )
    return BreakdownNode(
        key="classification",
        label=label,
        score=1.0 if matched else 0.0,
        weight=1.0,
        reason="single-label classification cell",
        children=[cell],
    )
