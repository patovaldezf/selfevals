"""TrajectoryGrader: output-state gate + diagnostic trajectory funnel.

The contract of this grader splits cleanly in two:

1. OUTPUT-STATE is authoritative. The pass/fail verdict comes entirely from
   an injectable ``output_grader`` (default :class:`DeterministicGrader`,
   which reads ``EvalCase.expected``). Whatever that grader returns IS the
   ``GradeResult.label``/``score`` of this grader. The trajectory never
   overrides it.

2. TRAJECTORY is diagnostic. Walking the trace in span order, the grader
   detects four trajectory failure modes and records each as a
   ``BreakdownNode`` child with ``weight=0`` (advisory). Advisory nodes
   roll up into the funnel for drill-down but, by the ``BreakdownNode``
   contract (weight 0 => no score contribution), they NEVER flip the label.

   The four diagnostic modes (each a stable failure-mode tag):
   - ``trajectory_wrong_tool_order``: a configured canonical tool order is
     not an ordered subsequence of the tools actually invoked.
   - ``trajectory_tool_loop_overrun``: the same tool runs more than
     ``max_consecutive`` times in a row, or more than ``max_total`` times
     overall.
   - ``trajectory_missing_routing_decision``: a ``HandoffSpan`` occurred
     (or ``expect_routing_decision=True``) with no ``DecisionSpan`` present.
   - ``trajectory_redundant_retrieval``: a ``RetrievalSpan`` is duplicated
     by ``query_hash`` (falling back to ``query_pointer``).

The ONLY trajectory signals that may flip the verdict are explicit
``HardInvariants`` (``forbidden_tools`` / ``max_tool_calls``). These are
intentionally narrow, opt-in trajectory invariants that a release must not
violate regardless of output quality; a violation forces ``FAIL``.

Everything here is agnostic: no external consumers, no domain assumptions.

The diagnostic/hard-invariant checks are split into `trajectory_checks.py`
(same free-function-delegate pattern used across the codebase's other
oversized classes) — this class wires that logic together, passing its
config (tool order, loop caps, hard invariants) in.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from selfevals.graders.base import BreakdownNode, GradeLabel, Grader, GraderContext, GradeResult
from selfevals.graders.deterministic import DeterministicGrader
from selfevals.graders.trajectory_checks import (
    FM_HARD_FORBIDDEN_TOOL,
    FM_HARD_MAX_TOOL_CALLS,
    FM_MISSING_ROUTING_DECISION,
    FM_REDUNDANT_RETRIEVAL,
    FM_TOOL_LOOP_OVERRUN,
    FM_WRONG_TOOL_ORDER,
    ROOT_KEY,
    check_hard_invariants,
    diagnose,
    tools_invoked,
)

__all__ = [
    "FM_HARD_FORBIDDEN_TOOL",
    "FM_HARD_MAX_TOOL_CALLS",
    "FM_MISSING_ROUTING_DECISION",
    "FM_REDUNDANT_RETRIEVAL",
    "FM_TOOL_LOOP_OVERRUN",
    "FM_WRONG_TOOL_ORDER",
    "HardInvariants",
    "TrajectoryGrader",
]


@dataclass(frozen=True)
class HardInvariants:
    """Opt-in trajectory invariants that DO flip the verdict to FAIL.

    Unlike the four diagnostic modes (advisory, weight 0), a violation of a
    hard invariant is blocking: it overrides an otherwise-passing
    output-state grade. Keep this set narrow on purpose -- it is the only
    way the trajectory may change the label.

    Fields:
    - ``forbidden_tools``: tool names that must never appear in the trace.
    - ``max_tool_calls``: maximum total number of ToolCallSpans allowed;
      ``None`` disables the check.
    """

    forbidden_tools: frozenset[str] = field(default_factory=frozenset)
    max_tool_calls: int | None = None


class TrajectoryGrader(Grader):
    """Grade output-state authoritatively; diagnose the trajectory advisorily.

    The verdict is delegated to ``output_grader`` (default
    :class:`DeterministicGrader`). Trajectory findings are attached as
    ``weight=0`` children under a ``trajectory`` breakdown root and never
    change the label -- except an explicit :class:`HardInvariants` violation,
    which forces ``FAIL``.
    """

    def __init__(
        self,
        name: str = "trajectory",
        *,
        output_grader: Grader | None = None,
        expected_tool_order: Sequence[str] | None = None,
        max_consecutive_tool_calls: int = 3,
        max_total_tool_calls: int | None = None,
        expect_routing_decision: bool = False,
        hard_invariants: HardInvariants | None = None,
    ) -> None:
        if not name:
            raise ValueError("grader name must be non-empty")
        if max_consecutive_tool_calls < 1:
            raise ValueError("max_consecutive_tool_calls must be >= 1")
        if max_total_tool_calls is not None and max_total_tool_calls < 0:
            raise ValueError("max_total_tool_calls must be >= 0")
        self.name = name
        self._output_grader: Grader = output_grader or DeterministicGrader()
        self._expected_tool_order = list(expected_tool_order) if expected_tool_order else []
        self._max_consecutive = max_consecutive_tool_calls
        self._max_total = max_total_tool_calls
        self._expect_routing_decision = expect_routing_decision
        self._hard = hard_invariants or HardInvariants()

    async def grade(self, context: GraderContext) -> GradeResult:
        # 1. Output-state verdict is authoritative.
        output = await self._output_grader.grade(context)

        # 2. Diagnostic trajectory modes -> advisory (weight=0) children.
        diagnostics = diagnose(
            context.trace,
            expected_tool_order=self._expected_tool_order,
            max_consecutive=self._max_consecutive,
            max_total=self._max_total,
            expect_routing_decision=self._expect_routing_decision,
        )
        children = [
            BreakdownNode(
                key=d.key,
                label=GradeLabel.FAIL,
                score=0.0,
                weight=0.0,
                reason=d.reason,
                failure_modes=[d.failure_mode],
            )
            for d in diagnostics
        ]

        # 3. Hard invariants -> the only trajectory signal that flips FAIL.
        hard_violations = check_hard_invariants(context.trace, hard=self._hard)

        label = output.label
        score = output.score
        failure_modes = list(output.failure_modes)
        reason = output.reason
        if hard_violations:
            label = GradeLabel.FAIL
            score = 0.0
            hard_modes = [v.failure_mode for v in hard_violations]
            failure_modes = sorted(set(failure_modes) | set(hard_modes))
            hard_reason = "; ".join(f"{v.failure_mode}:{v.reason}" for v in hard_violations)
            reason = (
                f"hard trajectory invariant violated: {hard_reason}"
                if not output.reason
                else f"{output.reason}; hard trajectory invariant violated: {hard_reason}"
            )
            # Hard violations are also recorded in the funnel, but as a real
            # (weight 1.0) FAIL node so the drill-down shows what flipped it.
            children.extend(
                BreakdownNode(
                    key=v.key,
                    label=GradeLabel.FAIL,
                    score=0.0,
                    weight=1.0,
                    reason=v.reason,
                    failure_modes=[v.failure_mode],
                )
                for v in hard_violations
            )

        breakdown = BreakdownNode(
            key=ROOT_KEY,
            label=label,
            score=score,
            weight=1.0,
            reason="output-state authoritative; trajectory children advisory",
            children=children,
        )

        details = {
            "output_grader": self._output_grader.name,
            "output_label": output.label.value,
            "tools_invoked": tools_invoked(context.trace),
            "trajectory_diagnostics": [d.failure_mode for d in diagnostics],
            "hard_invariant_violations": [v.failure_mode for v in hard_violations],
        }

        return GradeResult(
            grader=self.name,
            label=label,
            reason=reason,
            score=score,
            confidence=output.confidence,
            failure_modes=failure_modes,
            details=details,
            breakdown=breakdown,
        )
