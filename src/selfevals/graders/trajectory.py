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
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from selfevals.graders.base import (
    BreakdownNode,
    GradeLabel,
    Grader,
    GraderContext,
    GradeResult,
)
from selfevals.graders.deterministic import DeterministicGrader
from selfevals.schemas.trace import (
    DecisionSpan,
    HandoffSpan,
    RetrievalSpan,
    ToolCallSpan,
)

if TYPE_CHECKING:
    from selfevals.schemas.trace import Trace

# --- Failure-mode identifiers (stable; do not rename without a migration) ---

FM_WRONG_TOOL_ORDER = "trajectory_wrong_tool_order"
FM_TOOL_LOOP_OVERRUN = "trajectory_tool_loop_overrun"
FM_MISSING_ROUTING_DECISION = "trajectory_missing_routing_decision"
FM_REDUNDANT_RETRIEVAL = "trajectory_redundant_retrieval"
FM_HARD_FORBIDDEN_TOOL = "trajectory_hard_forbidden_tool"
FM_HARD_MAX_TOOL_CALLS = "trajectory_hard_max_tool_calls"

# Stable keys for the breakdown funnel nodes.
ROOT_KEY = "trajectory"
KEY_WRONG_TOOL_ORDER = "wrong_tool_order"
KEY_TOOL_LOOP_OVERRUN = "tool_loop_overrun"
KEY_MISSING_ROUTING_DECISION = "missing_routing_decision"
KEY_REDUNDANT_RETRIEVAL = "redundant_retrieval"


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


def _tools_invoked(trace: Trace) -> list[str]:
    """Tool names in span order (the trace records spans chronologically)."""
    return [s.tool_name for s in trace.spans if isinstance(s, ToolCallSpan)]


def _is_ordered_subsequence(needle: Sequence[str], haystack: Sequence[str]) -> bool:
    """True if ``needle`` appears as an ordered (not necessarily contiguous)
    subsequence of ``haystack``."""
    it = iter(haystack)
    return all(token in it for token in needle)


@dataclass(frozen=True)
class _Diagnostic:
    key: str
    failure_mode: str
    reason: str


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
        diagnostics = self._diagnose(context.trace)
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
        hard_violations = self._check_hard_invariants(context.trace)

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
            "tools_invoked": _tools_invoked(context.trace),
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

    # --- diagnostic trajectory modes (advisory) ---

    def _diagnose(self, trace: Trace) -> list[_Diagnostic]:
        out: list[_Diagnostic] = []
        invoked = _tools_invoked(trace)

        wrong_order = self._check_tool_order(invoked)
        if wrong_order is not None:
            out.append(wrong_order)

        loop = self._check_tool_loop(invoked)
        if loop is not None:
            out.append(loop)

        routing = self._check_missing_routing(trace)
        if routing is not None:
            out.append(routing)

        redundant = self._check_redundant_retrieval(trace)
        if redundant is not None:
            out.append(redundant)

        return out

    def _check_tool_order(self, invoked: list[str]) -> _Diagnostic | None:
        if not self._expected_tool_order:
            return None
        if _is_ordered_subsequence(self._expected_tool_order, invoked):
            return None
        return _Diagnostic(
            key=KEY_WRONG_TOOL_ORDER,
            failure_mode=FM_WRONG_TOOL_ORDER,
            reason=(
                f"expected tool order {self._expected_tool_order} is not an "
                f"ordered subsequence of invoked tools {invoked}"
            ),
        )

    def _check_tool_loop(self, invoked: list[str]) -> _Diagnostic | None:
        # Consecutive run-length over the configured cap.
        run_tool: str | None = None
        run_len = 0
        for tool in invoked:
            if tool == run_tool:
                run_len += 1
            else:
                run_tool = tool
                run_len = 1
            if run_len > self._max_consecutive:
                return _Diagnostic(
                    key=KEY_TOOL_LOOP_OVERRUN,
                    failure_mode=FM_TOOL_LOOP_OVERRUN,
                    reason=(
                        f"tool {tool!r} invoked {run_len} times consecutively "
                        f"(max_consecutive={self._max_consecutive})"
                    ),
                )
        # Total-count cap (per tool).
        if self._max_total is not None:
            counts: dict[str, int] = {}
            for tool in invoked:
                counts[tool] = counts.get(tool, 0) + 1
            for tool, count in counts.items():
                if count > self._max_total:
                    return _Diagnostic(
                        key=KEY_TOOL_LOOP_OVERRUN,
                        failure_mode=FM_TOOL_LOOP_OVERRUN,
                        reason=(
                            f"tool {tool!r} invoked {count} times total "
                            f"(max_total={self._max_total})"
                        ),
                    )
        return None

    def _check_missing_routing(self, trace: Trace) -> _Diagnostic | None:
        has_decision = any(isinstance(s, DecisionSpan) for s in trace.spans)
        if has_decision:
            return None
        has_handoff = any(isinstance(s, HandoffSpan) for s in trace.spans)
        if has_handoff or self._expect_routing_decision:
            trigger = "handoff present" if has_handoff else "routing decision expected"
            return _Diagnostic(
                key=KEY_MISSING_ROUTING_DECISION,
                failure_mode=FM_MISSING_ROUTING_DECISION,
                reason=f"no DecisionSpan recorded ({trigger})",
            )
        return None

    def _check_redundant_retrieval(self, trace: Trace) -> _Diagnostic | None:
        seen: set[str] = set()
        for s in trace.spans:
            if not isinstance(s, RetrievalSpan):
                continue
            # Identity by query_hash; fall back to query_pointer when absent.
            key = s.query_hash or s.query_pointer
            if key is None:
                continue
            if key in seen:
                return _Diagnostic(
                    key=KEY_REDUNDANT_RETRIEVAL,
                    failure_mode=FM_REDUNDANT_RETRIEVAL,
                    reason=f"retrieval query {key!r} issued more than once",
                )
            seen.add(key)
        return None

    # --- hard invariants (blocking) ---

    def _check_hard_invariants(self, trace: Trace) -> list[_Diagnostic]:
        out: list[_Diagnostic] = []
        invoked = _tools_invoked(trace)
        invoked_set = set(invoked)

        forbidden_hit = sorted(self._hard.forbidden_tools & invoked_set)
        for tool in forbidden_hit:
            out.append(
                _Diagnostic(
                    key=f"hard_forbidden_tool:{tool}",
                    failure_mode=FM_HARD_FORBIDDEN_TOOL,
                    reason=f"forbidden tool {tool!r} invoked",
                )
            )

        if self._hard.max_tool_calls is not None and len(invoked) > self._hard.max_tool_calls:
            out.append(
                _Diagnostic(
                    key="hard_max_tool_calls",
                    failure_mode=FM_HARD_MAX_TOOL_CALLS,
                    reason=(
                        f"{len(invoked)} tool calls exceed max_tool_calls="
                        f"{self._hard.max_tool_calls}"
                    ),
                )
            )

        return out
