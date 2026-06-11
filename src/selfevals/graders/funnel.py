"""FunnelGrader: declarative N-level funnel scoring.

A real agent is judged in *stages*: did it find the right entity (L1), then did
it resolve it correctly (L2), and was the resolution phrased well (L3)? A flat
grader collapses that into one pass/fail and throws away *where* the agent
failed. The funnel preserves it: each level scores one stage, a `gate` level
short-circuits its descendants when it fails (you don't grade the resolver if
the finder never found anything), and every level contributes a node to the
`BreakdownNode` tree the existing aggregator/reporter/frontend already roll up
and drill into — at arbitrary depth.

The funnel is **composition over graders**, the same shape as `JudgePanelGrader`:
it receives already-built `_Level`s (each carrying a sub-`Grader` as its match),
runs them, and folds their `GradeResult`s into one tree. It never builds its own
sub-graders — `runner.launch._funnel_factory` does that (where adapter
resolution for nested `llm_judge`/`judge_panel` lives).

A level's match is *any* `Grader`:
- a builtin match (this module's `_ExistsMatch`, `_EqualsMatch`, `_ByIndexMatch`,
  `_ByKeyMatch`, `_ToolCalledMatch`, `_SpanExistsMatch`), or a `SetMatchGrader`;
- a nested full grader referenced by name (`llm_judge`, `judge_panel`, even
  another `funnel`) — resolved through the registry by the factory.

Builtin matches and nested graders share the `Grader.grade -> GradeResult`
interface, so the funnel runtime treats them identically.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

from selfevals.graders._select import select
from selfevals.graders.base import (
    BreakdownNode,
    GradeLabel,
    Grader,
    GraderContext,
    GradeResult,
)
from selfevals.schemas.enums import SpanKind

_FAILING_LABELS = frozenset({GradeLabel.FAIL, GradeLabel.ERROR})


@dataclass(frozen=True)
class _Level:
    """One stage of a funnel. Built by the factory; held by the FunnelGrader.

    `key` is the stable identity of this level's BreakdownNode — the aggregator
    groups by it across cases, so it must be unique across the whole tree. The
    `match` is the already-constructed grader that scores this stage; `gate`
    means "if this level fails, skip my descendants". `feeds_extract` injects the
    level's extracted datum into a synthetic `detected` slot so a generic
    pre-declared grader (one with no `extract` of its own) can consume it.
    """

    key: str
    extract: str
    match: Grader
    gate: bool = False
    failure_mode: str | None = None
    feeds_extract: bool = False
    children: list[_Level] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Builtin matches — thin graders the funnel factory constructs inline.         #
# Each reads its slice via the `extract` path (or the trace) and returns a     #
# single-node GradeResult so the funnel can fold it like any other grader.     #
# --------------------------------------------------------------------------- #


def _structured(context: GraderContext) -> dict[str, object] | None:
    response = context.response
    if response is None or response.structured_output is None:
        return None
    return response.structured_output


def _leaf(name: str, *, passed: bool, reason: str, failure_mode: str) -> GradeResult:
    label = GradeLabel.PASS if passed else GradeLabel.FAIL
    return GradeResult(
        grader=name,
        label=label,
        reason=reason,
        score=1.0 if passed else 0.0,
        failure_modes=[] if passed else [failure_mode],
    )


class _ExistsMatch(Grader):
    """PASS iff the `extract` path resolves to a non-empty, non-None value."""

    def __init__(self, name: str, *, extract: str, failure_mode: str) -> None:
        self.name = name
        self._extract = extract
        self._fm = failure_mode

    async def grade(self, context: GraderContext) -> GradeResult:
        so = _structured(context)
        value = select(so, self._extract) if so is not None else None
        present = value is not None and value != [] and value != {} and value != ""
        return _leaf(
            self.name,
            passed=present,
            reason=f"{self._extract!r} {'present' if present else 'absent/empty'}",
            failure_mode=self._fm,
        )


class _EqualsMatch(Grader):
    """PASS iff `extract` equals `value` (optionally case-insensitive on str)."""

    def __init__(
        self, name: str, *, extract: str, value: object, case_sensitive: bool, failure_mode: str
    ) -> None:
        self.name = name
        self._extract = extract
        self._value = value
        self._case_sensitive = case_sensitive
        self._fm = failure_mode

    async def grade(self, context: GraderContext) -> GradeResult:
        so = _structured(context)
        actual = select(so, self._extract) if so is not None else None
        passed = _eq(actual, self._value, case_sensitive=self._case_sensitive)
        return _leaf(
            self.name,
            passed=passed,
            reason=f"{actual!r} {'==' if passed else '!='} {self._value!r}",
            failure_mode=self._fm,
        )


class _ByKeyMatch(Grader):
    """PASS iff `extract` is a dict whose `key` equals `value`."""

    def __init__(
        self,
        name: str,
        *,
        extract: str,
        key: str,
        value: object,
        case_sensitive: bool,
        failure_mode: str,
    ) -> None:
        self.name = name
        self._extract = extract
        self._key = key
        self._value = value
        self._case_sensitive = case_sensitive
        self._fm = failure_mode

    async def grade(self, context: GraderContext) -> GradeResult:
        so = _structured(context)
        target = select(so, self._extract) if so is not None else None
        actual = target.get(self._key) if isinstance(target, dict) else None
        passed = _eq(actual, self._value, case_sensitive=self._case_sensitive)
        return _leaf(
            self.name,
            passed=passed,
            reason=f"[{self._key!r}]={actual!r} {'==' if passed else '!='} {self._value!r}",
            failure_mode=self._fm,
        )


class _ByIndexMatch(Grader):
    """PASS iff `extract` is a list whose element at `index` equals `value`."""

    def __init__(
        self,
        name: str,
        *,
        extract: str,
        index: int,
        value: object,
        case_sensitive: bool,
        failure_mode: str,
    ) -> None:
        self.name = name
        self._extract = extract
        self._index = index
        self._value = value
        self._case_sensitive = case_sensitive
        self._fm = failure_mode

    async def grade(self, context: GraderContext) -> GradeResult:
        so = _structured(context)
        target = select(so, self._extract) if so is not None else None
        actual: object | None = None
        if isinstance(target, list) and -len(target) <= self._index < len(target):
            actual = target[self._index]
        passed = _eq(actual, self._value, case_sensitive=self._case_sensitive)
        return _leaf(
            self.name,
            passed=passed,
            reason=f"[{self._index}]={actual!r} {'==' if passed else '!='} {self._value!r}",
            failure_mode=self._fm,
        )


class _ToolCalledMatch(Grader):
    """PASS iff a ToolCallSpan named `tool` exists in the trace."""

    def __init__(self, name: str, *, tool: str, failure_mode: str) -> None:
        self.name = name
        self._tool = tool
        self._fm = failure_mode

    async def grade(self, context: GraderContext) -> GradeResult:
        called = any(getattr(span, "tool_name", None) == self._tool for span in context.trace.spans)
        return _leaf(
            self.name,
            passed=called,
            reason=f"tool {self._tool!r} {'called' if called else 'never called'}",
            failure_mode=self._fm,
        )


class _SpanExistsMatch(Grader):
    """PASS iff a span of `span_kind` exists in the trace."""

    def __init__(self, name: str, *, span_kind: SpanKind, failure_mode: str) -> None:
        self.name = name
        self._span_kind = span_kind
        self._fm = failure_mode

    async def grade(self, context: GraderContext) -> GradeResult:
        present = any(span.kind == self._span_kind for span in context.trace.spans)
        return _leaf(
            self.name,
            passed=present,
            reason=f"span {self._span_kind.value!r} {'present' if present else 'absent'}",
            failure_mode=self._fm,
        )


def _eq(actual: object, expected: object, *, case_sensitive: bool) -> bool:
    if not case_sensitive and isinstance(actual, str) and isinstance(expected, str):
        return actual.casefold() == expected.casefold()
    # Guard against Python's bool/int/float equality coercion: `1 == True` and
    # `0 == False` are both True, but a contract distinguishing a status code 1
    # from boolean true (or 0 from false) must not be graded as equal. A bool is
    # only equal to a bool.
    if isinstance(actual, bool) != isinstance(expected, bool):
        return False
    return bool(actual == expected)


# --------------------------------------------------------------------------- #
# The funnel grader itself.                                                    #
# --------------------------------------------------------------------------- #


class FunnelGrader(Grader):
    """Run N sequential levels, gating descendants, into one breakdown tree.

    Receives already-built `_Level`s (the factory in `runner.launch` constructs
    them, resolving nested graders). The top-level `GradeResult.label`/`score`
    stay authoritative; the funnel tree is the drill-down.
    """

    def __init__(self, name: str, *, levels: list[_Level]) -> None:
        if not name:
            raise ValueError("grader name must be non-empty")
        if not levels:
            raise ValueError("funnel must have at least one level")
        self.name = name
        self._levels = list(levels)
        keys = _all_keys(self._levels)
        if len(set(keys)) != len(keys):
            raise ValueError(f"funnel level keys must be unique across the tree; got {keys}")

    async def grade(self, context: GraderContext) -> GradeResult:
        nodes: list[BreakdownNode] = []
        evaluated: list[BreakdownNode] = []
        for level in self._levels:
            await _run_level(level, context, gated_off=False, out=nodes, evaluated=evaluated)

        # Global verdict: ERROR if any evaluated level errored; else FAIL if any
        # evaluated level failed; else PASS. Skipped (gated-off) levels do not
        # vote — the gate's own FAIL already represents the funnel failure.
        labels = [n.label for n in evaluated if n.label is not None]
        if GradeLabel.ERROR in labels:
            label = GradeLabel.ERROR
        elif GradeLabel.FAIL in labels:
            label = GradeLabel.FAIL
        else:
            label = GradeLabel.PASS

        scored = [n.score for n in evaluated if n.score is not None]
        score = sum(scored) / len(scored) if scored else None

        failure_modes: list[str] = []
        for node in evaluated:
            failure_modes.extend(node.failure_modes)

        # Total levels = the whole tree (top-level + nested), so the "evaluated
        # of total" reason is honest when a gate skips descendants. `evaluated`
        # already accumulates nested nodes; `nodes` holds only the top-level row.
        total_levels = len(_all_keys(self._levels))
        root = BreakdownNode(
            key="funnel",
            label=label,
            score=score,
            weight=1.0,
            reason=f"{len(evaluated)}/{total_levels} levels evaluated",
            failure_modes=list(dict.fromkeys(failure_modes)),
            children=nodes,
        )
        return GradeResult(
            grader=self.name,
            label=label,
            reason=root.reason,
            score=score,
            failure_modes=root.failure_modes,
            details={"levels": total_levels, "evaluated": len(evaluated)},
            breakdown=root,
        )


async def _run_level(
    level: _Level,
    context: GraderContext,
    *,
    gated_off: bool,
    out: list[BreakdownNode],
    evaluated: list[BreakdownNode],
) -> None:
    """Evaluate one level (and recurse into its children), appending its node.

    When `gated_off`, the level and all descendants are SKIPPED (weight=0,
    label SKIPPED) without calling `.grade` — and contribute nothing to the
    score or verdict.
    """
    if gated_off:
        node = _skipped_node(level)
        out.append(node)
        return

    level_ctx = _feed(level, context) if level.feeds_extract else context
    try:
        result = await level.match.grade(level_ctx)
    except Exception as exc:  # mirror panel's return_exceptions: a match crash → ERROR
        result = GradeResult(
            grader=level.match.name,
            label=GradeLabel.ERROR,
            reason=f"level match raised: {exc}",
        )

    failed = result.label in _FAILING_LABELS
    fms = _level_failure_modes(level, result, failed=failed)
    inner = result.breakdown.children if result.breakdown is not None else []

    # Build child level nodes first, so the funnel node owns its full subtree.
    child_nodes: list[BreakdownNode] = []
    descendants_gated = gated_off or (level.gate and failed)
    for child in level.children:
        await _run_level(
            child, context, gated_off=descendants_gated, out=child_nodes, evaluated=evaluated
        )

    node = BreakdownNode(
        key=level.key,
        label=result.label,
        score=result.score,
        weight=1.0,
        reason=result.reason,
        failure_modes=fms,
        children=[*inner, *child_nodes],
    )
    out.append(node)
    evaluated.append(node)


def _skipped_node(level: _Level) -> BreakdownNode:
    children = [_skipped_node(child) for child in level.children]
    return BreakdownNode(
        key=level.key,
        label=GradeLabel.SKIPPED,
        score=None,
        weight=0.0,
        reason="skipped: an upstream gate failed",
        children=children,
    )


def _level_failure_modes(level: _Level, result: GradeResult, *, failed: bool) -> list[str]:
    if not failed:
        return []
    if level.failure_mode is not None:
        return [level.failure_mode]
    if result.failure_modes:
        return list(result.failure_modes)
    return [f"funnel_{level.key}_fail"]


def _feed(level: _Level, context: GraderContext) -> GraderContext:
    """Inject the level's extracted datum into a synthetic `detected` slot.

    Lets a generic pre-declared grader (no `extract` of its own) consume this
    level's slice. Both `AdapterResponse` and `GraderContext` are frozen, so we
    rebuild via `dataclasses.replace`.
    """
    response = context.response
    if response is None or response.structured_output is None:
        return context
    extracted = select(response.structured_output, level.extract)
    synth_so = {**response.structured_output, "detected": extracted}
    synth_response = dataclasses.replace(response, structured_output=synth_so)
    return dataclasses.replace(context, response=synth_response)


def _all_keys(levels: list[_Level]) -> list[str]:
    keys: list[str] = []
    for level in levels:
        keys.append(level.key)
        keys.extend(_all_keys(level.children))
    return keys
