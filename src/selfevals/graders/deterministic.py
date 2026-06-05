"""DeterministicGrader: declarative rules from EvalCase.expected.

Rules supported in MVP:
- must_include: every string must appear in the final response (case-
  insensitive by default; controllable via constructor flag).
- must_not_include: none of the strings may appear.
- required_tools: every tool listed must appear in the trace.
- forbidden_tools: no tool listed may appear.
- regex_match: optional regex applied to the final response.
- structured_output equality: when EvalCase.expected.structured_output
  is set, the adapter's structured_output must match exactly.

Each rule has a stable failure-mode tag emitted in GradeResult.failure_modes
so weighted scoring can attribute failures upstream.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from selfevals.graders.base import (
    BreakdownNode,
    GradeLabel,
    Grader,
    GraderContext,
    GradeResult,
)
from selfevals.schemas.trace import LLMCallSpan, ToolCallSpan

if TYPE_CHECKING:
    from selfevals.schemas.eval_case import Expected
    from selfevals.schemas.trace import Trace


class DeterministicRuleViolationError(RuntimeError):
    """Raised if the grader is asked to evaluate a contradictory rule set."""


@dataclass(frozen=True)
class _Violation:
    failure_mode: str
    detail: str


def _final_response_text(ctx: GraderContext) -> str:
    if ctx.response is not None and ctx.response.content:
        return ctx.response.content
    # Fall back to the final structured output's "content" or empty.
    if ctx.response is not None and ctx.response.structured_output is not None:
        content = ctx.response.structured_output.get("content")
        if isinstance(content, str):
            return content
    return ""


def _tools_invoked(trace: Trace) -> list[str]:
    return [s.tool_name for s in trace.spans if isinstance(s, ToolCallSpan)]


def _llm_call_count(trace: Trace) -> int:
    return sum(1 for s in trace.spans if isinstance(s, LLMCallSpan))


def _leaf(key: str, *, passed: bool, reason: str, failure_mode: str | None = None) -> BreakdownNode:
    """One rule-instance node (e.g. a single `must_include` substring).

    `weight=0` so it is advisory: it drives the funnel drill-down without ever
    affecting the authoritative top-level score (per `BreakdownNode` contract).
    """
    return BreakdownNode(
        key=key,
        label=GradeLabel.PASS if passed else GradeLabel.FAIL,
        score=1.0 if passed else 0.0,
        weight=0.0,
        reason=reason,
        failure_modes=[] if passed or failure_mode is None else [failure_mode],
    )


def _build_breakdown(
    expected: Expected,
    *,
    label: GradeLabel,
    score: float,
    violated: set[str],
) -> BreakdownNode | None:
    """Decompose a deterministic grade into a funnel tree, one branch per rule
    dimension the case actually declares.

    `violated` is the set of `"{failure_mode}:{detail}"` keys that failed, so
    each leaf knows its own verdict. The tree is purely additive — the root
    carries the authoritative `label`/`score`; every dimension/leaf is advisory
    (`weight=0`). Returns None when the case declares no rules (nothing to
    decompose), so the funnel stays honestly empty rather than showing a hollow
    root.
    """
    dimensions: list[BreakdownNode] = []

    def _dimension(
        key: str, items: list[str], failure_mode: str, *, present_is_pass: bool
    ) -> None:
        if not items:
            return
        leaves: list[BreakdownNode] = []
        for item in items:
            failed = f"{failure_mode}:{item}" in violated
            leaves.append(
                _leaf(
                    item,
                    passed=not failed,
                    reason=f"{failure_mode.replace('_', ' ')}: {item}",
                    failure_mode=failure_mode,
                )
            )
        passed_count = sum(1 for n in leaves if n.label == GradeLabel.PASS)
        dimensions.append(
            BreakdownNode(
                key=key,
                label=GradeLabel.PASS if passed_count == len(leaves) else GradeLabel.FAIL,
                score=passed_count / len(leaves),
                weight=0.0,
                reason=f"{passed_count}/{len(leaves)} satisfied",
                children=leaves,
            )
        )

    _dimension(
        "must_include", list(expected.must_include), "missing_required_substring", present_is_pass=True
    )
    _dimension(
        "must_not_include",
        list(expected.must_not_include),
        "forbidden_substring",
        present_is_pass=False,
    )
    _dimension(
        "required_tools", list(expected.required_tools), "missing_required_tool", present_is_pass=True
    )
    _dimension(
        "forbidden_tools",
        list(expected.forbidden_tools),
        "forbidden_tool_invoked",
        present_is_pass=False,
    )

    if not dimensions:
        return None
    return BreakdownNode(
        key="deterministic",
        label=label,
        score=score,
        weight=1.0,
        reason="per-rule funnel; output-state authoritative",
        children=dimensions,
    )


class DeterministicGrader(Grader):
    def __init__(
        self,
        name: str = "deterministic",
        *,
        case_sensitive: bool = False,
        regex_match: re.Pattern[str] | str | None = None,
    ) -> None:
        if not name:
            raise ValueError("grader name must be non-empty")
        self.name = name
        self._case_sensitive = case_sensitive
        if isinstance(regex_match, str):
            self._regex: re.Pattern[str] | None = re.compile(regex_match)
        else:
            self._regex = regex_match

    async def grade(self, context: GraderContext) -> GradeResult:
        expected: Expected = context.case.expected
        violations: list[_Violation] = []

        text = _final_response_text(context)
        haystack = text if self._case_sensitive else text.lower()
        invoked = _tools_invoked(context.trace)
        invoked_set = set(invoked)

        missing_required = 0
        for needle in expected.must_include:
            probe = needle if self._case_sensitive else needle.lower()
            if probe not in haystack:
                missing_required += 1
                violations.append(
                    _Violation(failure_mode="missing_required_substring", detail=needle)
                )

        for needle in expected.must_not_include:
            probe = needle if self._case_sensitive else needle.lower()
            if probe in haystack:
                violations.append(_Violation(failure_mode="forbidden_substring", detail=needle))

        for required in expected.required_tools:
            if required not in invoked_set:
                violations.append(_Violation(failure_mode="missing_required_tool", detail=required))

        for forbidden in expected.forbidden_tools:
            if forbidden in invoked_set:
                violations.append(
                    _Violation(failure_mode="forbidden_tool_invoked", detail=forbidden)
                )

        if self._regex is not None and not self._regex.search(text):
            violations.append(_Violation(failure_mode="regex_mismatch", detail=self._regex.pattern))

        if expected.structured_output is not None:
            response_struct = context.response.structured_output if context.response else None
            if response_struct != expected.structured_output:
                violations.append(
                    _Violation(
                        failure_mode="structured_output_mismatch",
                        detail="expected != actual",
                    )
                )

        # Loose hint metrics that don't fail but are useful for debug.
        details: dict[str, Any] = {
            "tools_invoked": invoked,
            "llm_call_count": _llm_call_count(context.trace),
        }

        # Keys of the rules that failed, so the funnel breakdown can mark each
        # leaf pass/fail. Built once and shared across all return paths.
        violated = {f"{v.failure_mode}:{v.detail}" for v in violations}

        # Recall mode: when min_recall is set and there are must_include items,
        # the must_include dimension is graded by recall (fraction present) rather
        # than all-or-nothing. Missing substrings still emit their failure modes
        # (so diagnostics survive) but no longer force FAIL on their own; the
        # threshold decides. Precedence: hard violations (must_not_include,
        # required/forbidden tools, regex, structured output) ALWAYS take priority
        # — even if recall passes, any hard violation makes the grade FAIL.
        if expected.min_recall is not None and expected.must_include:
            total = len(expected.must_include)
            recall = (total - missing_required) / total
            details["recall"] = recall
            hard_violations = [
                v for v in violations if v.failure_mode != "missing_required_substring"
            ]
            recall_passes = recall >= expected.min_recall
            if recall_passes and not hard_violations:
                modes = sorted({v.failure_mode for v in violations})
                reason = f"recall {recall:.3f} >= min_recall {expected.min_recall:.3f}"
                return GradeResult(
                    grader=self.name,
                    label=GradeLabel.PASS,
                    reason=reason,
                    score=recall,
                    failure_modes=modes,
                    details=details,
                    breakdown=_build_breakdown(
                        expected, label=GradeLabel.PASS, score=recall, violated=violated
                    ),
                )
            modes = sorted({v.failure_mode for v in violations})
            if not recall_passes:
                reason = f"recall {recall:.3f} < min_recall {expected.min_recall:.3f}"
            else:
                reason = "; ".join(f"{v.failure_mode}:{v.detail}" for v in hard_violations)
            return GradeResult(
                grader=self.name,
                label=GradeLabel.FAIL,
                reason=reason,
                score=recall,
                failure_modes=modes,
                details=details,
                breakdown=_build_breakdown(
                    expected, label=GradeLabel.FAIL, score=recall, violated=violated
                ),
            )

        if not violations:
            return GradeResult(
                grader=self.name,
                label=GradeLabel.PASS,
                reason="all deterministic rules satisfied",
                score=1.0,
                details=details,
                breakdown=_build_breakdown(
                    expected, label=GradeLabel.PASS, score=1.0, violated=violated
                ),
            )
        modes = sorted({v.failure_mode for v in violations})
        reason = "; ".join(f"{v.failure_mode}:{v.detail}" for v in violations)
        return GradeResult(
            grader=self.name,
            label=GradeLabel.FAIL,
            reason=reason,
            score=0.0,
            failure_modes=modes,
            details=details,
            breakdown=_build_breakdown(
                expected, label=GradeLabel.FAIL, score=0.0, violated=violated
            ),
        )
