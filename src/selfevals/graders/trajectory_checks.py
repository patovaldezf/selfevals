"""Free functions for `TrajectoryGrader`'s diagnostic + hard-invariant checks.

Split out of `trajectory.py` (same free-function-delegate pattern used
across the codebase's other oversized classes): each check is pure given the
trace and the grader's config (tool order, loop caps, hard invariants).
`TrajectoryGrader` stays the single `Grader` wiring these together.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from selfevals.schemas.trace import DecisionSpan, HandoffSpan, RetrievalSpan, ToolCallSpan

if TYPE_CHECKING:
    from selfevals.graders.trajectory import HardInvariants
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
class Diagnostic:
    key: str
    failure_mode: str
    reason: str


def tools_invoked(trace: Trace) -> list[str]:
    """Tool names in span order (the trace records spans chronologically)."""
    return [s.tool_name for s in trace.spans if isinstance(s, ToolCallSpan)]


def _is_ordered_subsequence(needle: Sequence[str], haystack: Sequence[str]) -> bool:
    """True if ``needle`` appears as an ordered (not necessarily contiguous)
    subsequence of ``haystack``."""
    it = iter(haystack)
    return all(token in it for token in needle)


def diagnose(
    trace: Trace,
    *,
    expected_tool_order: Sequence[str],
    max_consecutive: int,
    max_total: int | None,
    expect_routing_decision: bool,
) -> list[Diagnostic]:
    invoked = tools_invoked(trace)
    out: list[Diagnostic] = []

    wrong_order = check_tool_order(invoked, expected_tool_order=expected_tool_order)
    if wrong_order is not None:
        out.append(wrong_order)

    loop = check_tool_loop(invoked, max_consecutive=max_consecutive, max_total=max_total)
    if loop is not None:
        out.append(loop)

    routing = check_missing_routing(trace, expect_routing_decision=expect_routing_decision)
    if routing is not None:
        out.append(routing)

    redundant = check_redundant_retrieval(trace)
    if redundant is not None:
        out.append(redundant)

    return out


def check_tool_order(
    invoked: list[str], *, expected_tool_order: Sequence[str]
) -> Diagnostic | None:
    if not expected_tool_order:
        return None
    if _is_ordered_subsequence(expected_tool_order, invoked):
        return None
    return Diagnostic(
        key=KEY_WRONG_TOOL_ORDER,
        failure_mode=FM_WRONG_TOOL_ORDER,
        reason=(
            f"expected tool order {list(expected_tool_order)} is not an "
            f"ordered subsequence of invoked tools {invoked}"
        ),
    )


def check_tool_loop(
    invoked: list[str], *, max_consecutive: int, max_total: int | None
) -> Diagnostic | None:
    # Consecutive run-length over the configured cap.
    run_tool: str | None = None
    run_len = 0
    for tool in invoked:
        if tool == run_tool:
            run_len += 1
        else:
            run_tool = tool
            run_len = 1
        if run_len > max_consecutive:
            return Diagnostic(
                key=KEY_TOOL_LOOP_OVERRUN,
                failure_mode=FM_TOOL_LOOP_OVERRUN,
                reason=(
                    f"tool {tool!r} invoked {run_len} times consecutively "
                    f"(max_consecutive={max_consecutive})"
                ),
            )
    # Total-count cap (per tool).
    if max_total is not None:
        counts: dict[str, int] = {}
        for tool in invoked:
            counts[tool] = counts.get(tool, 0) + 1
        for tool, count in counts.items():
            if count > max_total:
                return Diagnostic(
                    key=KEY_TOOL_LOOP_OVERRUN,
                    failure_mode=FM_TOOL_LOOP_OVERRUN,
                    reason=f"tool {tool!r} invoked {count} times total (max_total={max_total})",
                )
    return None


def check_missing_routing(trace: Trace, *, expect_routing_decision: bool) -> Diagnostic | None:
    has_decision = any(isinstance(s, DecisionSpan) for s in trace.spans)
    if has_decision:
        return None
    has_handoff = any(isinstance(s, HandoffSpan) for s in trace.spans)
    if has_handoff or expect_routing_decision:
        trigger = "handoff present" if has_handoff else "routing decision expected"
        return Diagnostic(
            key=KEY_MISSING_ROUTING_DECISION,
            failure_mode=FM_MISSING_ROUTING_DECISION,
            reason=f"no DecisionSpan recorded ({trigger})",
        )
    return None


def check_redundant_retrieval(trace: Trace) -> Diagnostic | None:
    seen: set[str] = set()
    for s in trace.spans:
        if not isinstance(s, RetrievalSpan):
            continue
        # Identity by query_hash; fall back to query_pointer when absent.
        key = s.query_hash or s.query_pointer
        if key is None:
            continue
        if key in seen:
            return Diagnostic(
                key=KEY_REDUNDANT_RETRIEVAL,
                failure_mode=FM_REDUNDANT_RETRIEVAL,
                reason=f"retrieval query {key!r} issued more than once",
            )
        seen.add(key)
    return None


def check_hard_invariants(trace: Trace, *, hard: HardInvariants) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    invoked = tools_invoked(trace)
    invoked_set = set(invoked)

    forbidden_hit = sorted(hard.forbidden_tools & invoked_set)
    for tool in forbidden_hit:
        out.append(
            Diagnostic(
                key=f"hard_forbidden_tool:{tool}",
                failure_mode=FM_HARD_FORBIDDEN_TOOL,
                reason=f"forbidden tool {tool!r} invoked",
            )
        )

    if hard.max_tool_calls is not None and len(invoked) > hard.max_tool_calls:
        out.append(
            Diagnostic(
                key="hard_max_tool_calls",
                failure_mode=FM_HARD_MAX_TOOL_CALLS,
                reason=f"{len(invoked)} tool calls exceed max_tool_calls={hard.max_tool_calls}",
            )
        )

    return out
