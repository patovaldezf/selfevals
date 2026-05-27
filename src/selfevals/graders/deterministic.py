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
from typing import TYPE_CHECKING

from selfevals.graders.base import GradeLabel, Grader, GraderContext, GradeResult
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

    def grade(self, context: GraderContext) -> GradeResult:
        expected: Expected = context.case.expected
        violations: list[_Violation] = []

        text = _final_response_text(context)
        haystack = text if self._case_sensitive else text.lower()
        invoked = _tools_invoked(context.trace)
        invoked_set = set(invoked)

        for needle in expected.must_include:
            probe = needle if self._case_sensitive else needle.lower()
            if probe not in haystack:
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
        details = {
            "tools_invoked": invoked,
            "llm_call_count": _llm_call_count(context.trace),
        }
        if not violations:
            return GradeResult(
                grader=self.name,
                label=GradeLabel.PASS,
                reason="all deterministic rules satisfied",
                score=1.0,
                details=details,
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
        )
