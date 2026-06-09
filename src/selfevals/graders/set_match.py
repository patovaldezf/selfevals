"""SetMatchGrader: many-to-many (set-vs-set) scoring.

The ground truth for tasks like intention-detection or entity-extraction is a
**set**, not a single label: one message carries N intentions at once
("price check" + "inventory check" + "quote request"). Exact-match collapses
"2 of 3 detected" to a flat FAIL — destroying the very signal an eval exists to
capture. `min_recall` on the deterministic grader approximates recall but not
precision/F1.

This grader compares the agent's **detected** set against the case's
**expected** set and reports the orthogonal dimensions a set permits:

    completeness = |detected ∩ expected| / |expected|   # recall — got everything?
    precision    = |detected ∩ expected| / |detected|   # invented nothing extra?
    recall       = completeness
    f1           = 2·P·R / (P + R)

selfevals stays the scoring authority and stays domain-agnostic: any canonical
normalization (legacy aliases, casing) lives in the *case* via
`EvalCase.expected.aliases`, never hard-coded here. PASS/FAIL is gated by a
configurable dimension (`completeness` ≥ 1.0 by default, or `f1` ≥ threshold).

Detected set source: `AdapterResponse.structured_output["detected"]` (a list).
Expected set source: `EvalCase.expected.must_include` (reuses the existing
field — the set the case already declares as required).

The breakdown tree mirrors `deterministic._build_breakdown`: an authoritative
root (`weight=1.0`) over advisory per-dimension and per-element leaves
(`weight=0`), so the frontend funnel drill-down renders it unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from selfevals.graders.base import (
    BreakdownNode,
    GradeLabel,
    Grader,
    GraderContext,
    GradeResult,
)

if TYPE_CHECKING:
    from selfevals.schemas.eval_case import Expected

GatingDimension = Literal["completeness", "precision", "recall", "f1"]

_VALID_GATINGS: frozenset[str] = frozenset({"completeness", "precision", "recall", "f1"})

FM_MISSING = "set_match_missing"
"""Expected label that was not detected (a recall miss)."""
FM_EXTRANEOUS = "set_match_extraneous"
"""Detected label that was not expected (a precision miss)."""
FM_NO_DETECTED = "set_match_no_detected"
"""The response carried no usable `structured_output['detected']` list."""


@dataclass(frozen=True)
class _Scores:
    completeness: float
    precision: float
    recall: float
    f1: float

    def get(self, dimension: GatingDimension) -> float:
        value: float = getattr(self, dimension)
        return value


def _normalize(items: list[str], aliases: dict[str, str], *, case_sensitive: bool) -> list[str]:
    """Map each item through the case's alias table, then optionally fold case.

    Aliases are matched on the raw label first (so a case can map `price_check`
    regardless of casing policy); the case fold, when enabled, applies to the
    post-alias value so comparison is consistent across both sets.
    """
    out: list[str] = []
    for raw in items:
        mapped = aliases.get(raw, raw)
        out.append(mapped if case_sensitive else mapped.lower())
    return out


def _extract_detected(context: GraderContext) -> list[str] | None:
    """Pull the detected set from `structured_output['detected']`.

    Returns None (not []) when the key is absent or not a list of strings, so
    the caller can distinguish "agent produced nothing usable" (hard FAIL) from
    "agent legitimately detected the empty set".
    """
    response = context.response
    if response is None or response.structured_output is None:
        return None
    detected = response.structured_output.get("detected")
    if not isinstance(detected, list):
        return None
    if not all(isinstance(x, str) for x in detected):
        return None
    return list(detected)


def _compute_scores(detected: list[str], expected: list[str]) -> _Scores:
    detected_set = set(detected)
    expected_set = set(expected)
    intersection = detected_set & expected_set
    inter = len(intersection)
    completeness = inter / len(expected_set) if expected_set else 1.0
    precision = inter / len(detected_set) if detected_set else 1.0
    recall = completeness
    denom = precision + recall
    f1 = (2 * precision * recall / denom) if denom > 0 else 0.0
    return _Scores(completeness=completeness, precision=precision, recall=recall, f1=f1)


def _element_leaves(
    items: list[str],
    *,
    counterpart: set[str],
    failure_mode: str,
) -> list[BreakdownNode]:
    """One advisory leaf per element: PASS when it lands in the intersection.

    Used both for expected-elements (under completeness, miss = `set_match_missing`)
    and detected-elements (under precision, miss = `set_match_extraneous`).
    """
    leaves: list[BreakdownNode] = []
    for item in items:
        present = item in counterpart
        leaves.append(
            BreakdownNode(
                key=item,
                label=GradeLabel.PASS if present else GradeLabel.FAIL,
                score=1.0 if present else 0.0,
                weight=0.0,
                reason=("matched" if present else f"{failure_mode.replace('_', ' ')}"),
                failure_modes=[] if present else [failure_mode],
            )
        )
    return leaves


def _build_breakdown(
    *,
    label: GradeLabel,
    gating_score: float,
    scores: _Scores,
    detected: list[str],
    expected: list[str],
) -> BreakdownNode:
    """Funnel tree: authoritative root over advisory dimension/element leaves."""
    detected_set = set(detected)
    expected_set = set(expected)

    completeness_node = BreakdownNode(
        key="completeness",
        label=GradeLabel.PASS if scores.completeness >= 1.0 else GradeLabel.FAIL,
        score=scores.completeness,
        weight=0.0,
        reason=f"{len(detected_set & expected_set)}/{len(expected_set)} expected detected",
        children=_element_leaves(expected, counterpart=detected_set, failure_mode=FM_MISSING),
    )
    precision_node = BreakdownNode(
        key="precision",
        label=GradeLabel.PASS if scores.precision >= 1.0 else GradeLabel.FAIL,
        score=scores.precision,
        weight=0.0,
        reason=f"{len(detected_set & expected_set)}/{len(detected_set)} detected were expected",
        children=_element_leaves(detected, counterpart=expected_set, failure_mode=FM_EXTRANEOUS),
    )
    recall_node = BreakdownNode(
        key="recall",
        score=scores.recall,
        weight=0.0,
        reason="alias of completeness",
    )
    f1_node = BreakdownNode(
        key="f1",
        score=scores.f1,
        weight=0.0,
        reason="harmonic mean of precision and recall",
    )
    return BreakdownNode(
        key="set_match",
        label=label,
        score=gating_score,
        weight=1.0,
        reason="set-membership funnel; gating dimension authoritative",
        children=[completeness_node, precision_node, recall_node, f1_node],
    )


def _collect_failure_modes(detected: list[str], expected: list[str]) -> list[str]:
    detected_set = set(detected)
    expected_set = set(expected)
    modes: list[str] = []
    if expected_set - detected_set:
        modes.append(FM_MISSING)
    if detected_set - expected_set:
        modes.append(FM_EXTRANEOUS)
    return modes


class SetMatchGrader(Grader):
    """Score a detected set against an expected set (completeness/precision/F1).

    No-arg constructible (registry- and dotted-path-friendly). The gating
    dimension and threshold can be baked in by a YAML `set_match` grader spec;
    the defaults (`completeness` ≥ 1.0) mean "PASS iff every expected label was
    detected", the strictest and most common contract.
    """

    def __init__(
        self,
        name: str = "set_match",
        *,
        gating: GatingDimension = "completeness",
        threshold: float = 1.0,
        case_sensitive: bool = False,
    ) -> None:
        if not name:
            raise ValueError("grader name must be non-empty")
        if gating not in _VALID_GATINGS:
            raise ValueError(
                f"set_match gating must be one of {sorted(_VALID_GATINGS)}; got {gating!r}"
            )
        if not 0.0 <= threshold <= 1.0:
            raise ValueError(f"set_match threshold must be in [0, 1]; got {threshold!r}")
        self.name = name
        self._gating: GatingDimension = gating
        self._threshold = threshold
        self._case_sensitive = case_sensitive

    async def grade(self, context: GraderContext) -> GradeResult:
        expected_spec: Expected = context.case.expected
        aliases = expected_spec.aliases

        raw_detected = _extract_detected(context)
        if raw_detected is None:
            return GradeResult(
                grader=self.name,
                label=GradeLabel.FAIL,
                reason="no usable structured_output['detected'] list on the response",
                score=0.0,
                failure_modes=[FM_NO_DETECTED],
                details={"gating": self._gating, "threshold": self._threshold},
            )

        detected = _normalize(raw_detected, aliases, case_sensitive=self._case_sensitive)
        expected = _normalize(
            list(expected_spec.must_include), aliases, case_sensitive=self._case_sensitive
        )

        scores = _compute_scores(detected, expected)
        gating_score = scores.get(self._gating)
        passed = gating_score >= self._threshold
        label = GradeLabel.PASS if passed else GradeLabel.FAIL

        failure_modes = [] if passed else _collect_failure_modes(detected, expected)
        details: dict[str, Any] = {
            "completeness": scores.completeness,
            "precision": scores.precision,
            "recall": scores.recall,
            "f1": scores.f1,
            "gating": self._gating,
            "threshold": self._threshold,
            "detected": detected,
            "expected": expected,
        }
        reason = (
            f"{self._gating} {gating_score:.3f} "
            f"{'>=' if passed else '<'} threshold {self._threshold:.3f}"
        )
        return GradeResult(
            grader=self.name,
            label=label,
            reason=reason,
            score=gating_score,
            failure_modes=failure_modes,
            details=details,
            breakdown=_build_breakdown(
                label=label,
                gating_score=gating_score,
                scores=scores,
                detected=detected,
                expected=expected,
            ),
        )
