from __future__ import annotations

from datetime import UTC, datetime

import pytest

from selfevals.graders._select import select
from selfevals.graders.base import (
    BreakdownNode,
    GradeLabel,
    Grader,
    GraderContext,
    GradeResult,
)
from selfevals.graders.funnel import (
    FunnelGrader,
    _ByIndexMatch,
    _ByKeyMatch,
    _EqualsMatch,
    _ExistsMatch,
    _Level,
    _SpanExistsMatch,
    _ToolCalledMatch,
)
from selfevals.graders.set_match import SetMatchGrader
from selfevals.runner.adapters import AdapterResponse
from selfevals.schemas.enums import (
    DatasetSource,
    DatasetType,
    GroundTruthMethod,
    Level,
    SandboxMode,
    SpanKind,
    TraceState,
)
from selfevals.schemas.eval_case import (
    CaseTaxonomy,
    EvalCase,
    Expected,
    FeatureTag,
    GroundTruthSpec,
    SourceInfo,
)
from selfevals.schemas.trace import (
    AgentSnapshotRef,
    AgentTurnSpan,
    EnvironmentInfo,
    FinalState,
    RunInfo,
    Span,
    ToolCallSpan,
    Trace,
)

WS = "ws_funnel"
T0 = datetime(2025, 1, 1, tzinfo=UTC)


def _case(expected: Expected | None = None) -> EvalCase:
    return EvalCase(
        id=EvalCase.make_id(),
        workspace_id=WS,
        name="t",
        task_type="identify",
        input={"messages": [{"role": "user", "content": "hi"}]},
        taxonomy=CaseTaxonomy(
            level=Level.FINAL_RESPONSE,
            feature=FeatureTag(primary="identify.customer"),
            source=SourceInfo(type=DatasetSource.HANDCRAFTED),
            ground_truth=GroundTruthSpec(methods=[GroundTruthMethod.EXACT_MATCH]),
            dataset_type=DatasetType.CAPABILITY,
        ),
        expected=expected or Expected(),
    )


def _trace(spans: list[Span] | None = None) -> Trace:
    return Trace(
        id=Trace.make_id(),
        workspace_id=WS,
        run=RunInfo(run_id="run_01"),
        agent=AgentSnapshotRef(agent_id="ag_x", agent_version=1),
        environment=EnvironmentInfo(
            framework_version="selfevals/0.10.0",
            runtime="python-3.12",
            sandbox=SandboxMode.MOCK,
            started_at=T0,
        ),
        final_state=FinalState(status=TraceState.COMPLETED),
        spans=spans if spans is not None else [AgentTurnSpan(id="sp", name="t", started_at=T0)],
    )


def _ctx(
    structured: dict[str, object] | None = None,
    *,
    case: EvalCase | None = None,
    spans: list[Span] | None = None,
) -> GraderContext:
    return GraderContext(
        case=case or _case(),
        trace=_trace(spans),
        response=AdapterResponse(content=None, structured_output=structured),
    )


class SpyGrader(Grader):
    """Records whether .grade was called; returns a fixed verdict."""

    def __init__(self, name: str, label: GradeLabel, *, breakdown: BreakdownNode | None = None):
        self.name = name
        self._label = label
        self._breakdown = breakdown
        self.called = False

    async def grade(self, context: GraderContext) -> GradeResult:
        self.called = True
        return GradeResult(
            grader=self.name,
            label=self._label,
            reason="spy",
            score=1.0 if self._label is GradeLabel.PASS else 0.0,
            breakdown=self._breakdown,
        )


def _child(node: BreakdownNode, key: str) -> BreakdownNode:
    for c in node.children:
        if c.key == key:
            return c
    raise AssertionError(f"no child {key!r} in {[c.key for c in node.children]}")


# --------------------------------------------------------------------------- #
# Core funnel behaviour                                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_two_level_all_pass() -> None:
    finder = _Level(
        key="finder",
        extract="candidates[].id",
        match=SetMatchGrader(name="finder.sm", extract="candidates[].id"),
        gate=True,
        children=[
            _Level(
                key="resolver",
                extract="resolved.id",
                match=_EqualsMatch(
                    "resolver.eq",
                    extract="resolved.id",
                    value="abc",
                    case_sensitive=False,
                    failure_mode="resolver_wrong",
                ),
            )
        ],
    )
    grader = FunnelGrader("identify_funnel", levels=[finder])
    case = _case(Expected(must_include=["abc", "def"]))
    structured: dict[str, object] = {
        "candidates": [{"id": "abc"}, {"id": "def"}],
        "resolved": {"id": "abc"},
    }
    result = await grader.grade(_ctx(structured, case=case))
    assert result.label is GradeLabel.PASS
    assert result.breakdown is not None
    finder_node = _child(result.breakdown, "finder")
    assert finder_node.label is GradeLabel.PASS
    # nested set_match leaves rode up under the level node
    assert any(c.key == "completeness" for c in finder_node.children)
    resolver_node = _child(finder_node, "resolver")
    assert resolver_node.label is GradeLabel.PASS


@pytest.mark.asyncio
async def test_gate_fail_skips_descendants() -> None:
    resolver_spy = SpyGrader("resolver", GradeLabel.PASS)
    finder = _Level(
        key="finder",
        extract="x",
        match=SpyGrader("finder", GradeLabel.FAIL),
        gate=True,
        failure_mode="finder_missed",
        children=[_Level(key="resolver", extract="y", match=resolver_spy)],
    )
    grader = FunnelGrader("f", levels=[finder])
    result = await grader.grade(_ctx({}))
    assert result.label is GradeLabel.FAIL
    assert resolver_spy.called is False  # gated off — never graded
    assert "finder_missed" in result.failure_modes
    assert result.breakdown is not None
    resolver_node = _child(_child(result.breakdown, "finder"), "resolver")
    assert resolver_node.label is GradeLabel.SKIPPED
    assert resolver_node.weight == 0.0


@pytest.mark.asyncio
async def test_non_gate_fail_does_not_skip_descendants() -> None:
    resolver_spy = SpyGrader("resolver", GradeLabel.PASS)
    finder = _Level(
        key="finder",
        extract="x",
        match=SpyGrader("finder", GradeLabel.FAIL),
        gate=False,  # failure does NOT gate
        children=[_Level(key="resolver", extract="y", match=resolver_spy)],
    )
    result = await FunnelGrader("f", levels=[finder]).grade(_ctx({}))
    assert resolver_spy.called is True
    assert result.label is GradeLabel.FAIL  # finder still failed


@pytest.mark.asyncio
async def test_independent_siblings_both_evaluated() -> None:
    a = SpyGrader("a", GradeLabel.FAIL)
    b = SpyGrader("b", GradeLabel.PASS)
    levels = [
        _Level(key="a", extract="x", match=a, gate=False),
        _Level(key="b", extract="y", match=b, gate=False),
    ]
    result = await FunnelGrader("f", levels=levels).grade(_ctx({}))
    assert a.called and b.called
    assert result.label is GradeLabel.FAIL


@pytest.mark.asyncio
async def test_three_level_middle_gate_skips_only_deepest() -> None:
    deepest = SpyGrader("l3", GradeLabel.PASS)
    levels = [
        _Level(
            key="l1",
            extract="x",
            match=SpyGrader("l1", GradeLabel.PASS),
            gate=True,
            children=[
                _Level(
                    key="l2",
                    extract="y",
                    match=SpyGrader("l2", GradeLabel.FAIL),
                    gate=True,
                    children=[_Level(key="l3", extract="z", match=deepest)],
                )
            ],
        )
    ]
    result = await FunnelGrader("f", levels=levels).grade(_ctx({}))
    assert deepest.called is False
    assert result.label is GradeLabel.FAIL
    assert result.breakdown is not None
    l3 = _child(_child(_child(result.breakdown, "l1"), "l2"), "l3")
    assert l3.label is GradeLabel.SKIPPED


@pytest.mark.asyncio
async def test_match_raising_yields_error() -> None:
    class Boom(Grader):
        name = "boom"

        async def grade(self, context: GraderContext) -> GradeResult:
            raise RuntimeError("kaboom")

    result = await FunnelGrader(
        "f", levels=[_Level(key="x", extract="a", match=Boom())]
    ).grade(_ctx({}))
    assert result.label is GradeLabel.ERROR


@pytest.mark.asyncio
async def test_nested_grader_breakdown_children_surface() -> None:
    # The sub-grader's breakdown *children* ride up under the level node (the
    # level node itself already carries the sub-grader's label/score, so its
    # root would be redundant).
    inner = BreakdownNode(
        key="sub",
        children=[BreakdownNode(key="completeness", score=1.0), BreakdownNode(key="precision")],
    )
    spy = SpyGrader("g", GradeLabel.PASS, breakdown=inner)
    result = await FunnelGrader(
        "f", levels=[_Level(key="lvl", extract="a", match=spy)]
    ).grade(_ctx({}))
    assert result.breakdown is not None
    lvl = _child(result.breakdown, "lvl")
    keys = {c.key for c in lvl.children}
    assert {"completeness", "precision"} <= keys


@pytest.mark.asyncio
async def test_feeds_extract_injects_detected() -> None:
    # A pre-declared set_match with NO custom extract (reads "detected"); the
    # level feeds its slice into the synthetic detected slot.
    case = _case(Expected(must_include=["p1", "p2"]))
    level = _Level(
        key="finder",
        extract="products[].id",
        match=SetMatchGrader(name="sm"),  # default extract="detected"
        feeds_extract=True,
    )
    structured: dict[str, object] = {"products": [{"id": "p1"}, {"id": "p2"}]}
    result = await FunnelGrader("f", levels=[level]).grade(_ctx(structured, case=case))
    assert result.label is GradeLabel.PASS


# --------------------------------------------------------------------------- #
# Builtin matches                                                              #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_exists_match() -> None:
    m = _ExistsMatch("e", extract="resolved", failure_mode="absent")
    assert (await m.grade(_ctx({"resolved": {"id": "x"}}))).label is GradeLabel.PASS
    assert (await m.grade(_ctx({"resolved": None}))).label is GradeLabel.FAIL
    assert (await m.grade(_ctx({}))).label is GradeLabel.FAIL


@pytest.mark.asyncio
async def test_equals_match_casefold() -> None:
    m = _EqualsMatch("eq", extract="status", value="OK", case_sensitive=False, failure_mode="ne")
    assert (await m.grade(_ctx({"status": "ok"}))).label is GradeLabel.PASS
    m2 = _EqualsMatch("eq", extract="status", value="OK", case_sensitive=True, failure_mode="ne")
    assert (await m2.grade(_ctx({"status": "ok"}))).label is GradeLabel.FAIL


@pytest.mark.asyncio
async def test_by_index_match() -> None:
    m = _ByIndexMatch(
        "bi", extract="products", index=0, value="p1", case_sensitive=False, failure_mode="bad"
    )
    assert (await m.grade(_ctx({"products": ["p1", "p2"]}))).label is GradeLabel.PASS
    assert (await m.grade(_ctx({"products": ["p9"]}))).label is GradeLabel.FAIL
    assert (await m.grade(_ctx({"products": []}))).label is GradeLabel.FAIL


@pytest.mark.asyncio
async def test_by_key_match() -> None:
    m = _ByKeyMatch(
        "bk", extract="resolved", key="id", value="abc", case_sensitive=False, failure_mode="bad"
    )
    assert (await m.grade(_ctx({"resolved": {"id": "abc"}}))).label is GradeLabel.PASS
    assert (await m.grade(_ctx({"resolved": {"id": "xyz"}}))).label is GradeLabel.FAIL


@pytest.mark.asyncio
async def test_tool_called_match() -> None:
    m = _ToolCalledMatch("tc", tool="search", failure_mode="no_tool")
    with_tool: list[Span] = [ToolCallSpan(id="s1", name="search", started_at=T0, tool_name="search")]
    assert (await m.grade(_ctx(spans=with_tool))).label is GradeLabel.PASS
    assert (await m.grade(_ctx())).label is GradeLabel.FAIL


@pytest.mark.asyncio
async def test_span_exists_match() -> None:
    m = _SpanExistsMatch("se", span_kind=SpanKind.TOOL_CALL, failure_mode="no_span")
    with_tool: list[Span] = [ToolCallSpan(id="s1", name="search", started_at=T0, tool_name="search")]
    assert (await m.grade(_ctx(spans=with_tool))).label is GradeLabel.PASS
    assert (await m.grade(_ctx())).label is GradeLabel.FAIL  # only an AgentTurnSpan


# --------------------------------------------------------------------------- #
# Validation                                                                   #
# --------------------------------------------------------------------------- #


def test_empty_levels_rejected() -> None:
    with pytest.raises(ValueError, match="at least one level"):
        FunnelGrader("f", levels=[])


def test_empty_name_rejected() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        FunnelGrader("", levels=[_Level(key="x", extract="a", match=SpyGrader("g", GradeLabel.PASS))])


def test_duplicate_keys_across_tree_rejected() -> None:
    levels = [
        _Level(
            key="dup",
            extract="a",
            match=SpyGrader("g1", GradeLabel.PASS),
            children=[_Level(key="dup", extract="b", match=SpyGrader("g2", GradeLabel.PASS))],
        )
    ]
    with pytest.raises(ValueError, match="unique"):
        FunnelGrader("f", levels=levels)


def test_select_used_by_feeds() -> None:
    # sanity: the projection the feed relies on resolves as expected
    assert select({"products": [{"id": "p1"}]}, "products[].id") == ["p1"]


@pytest.mark.asyncio
async def test_equals_does_not_coerce_bool_and_int() -> None:
    # `1 == True` in Python; the grader must NOT treat them as equal.
    m = _EqualsMatch("eq", extract="flag", value=True, case_sensitive=False, failure_mode="ne")
    assert (await m.grade(_ctx({"flag": 1}))).label is GradeLabel.FAIL
    assert (await m.grade(_ctx({"flag": True}))).label is GradeLabel.PASS
    m0 = _EqualsMatch("eq", extract="n", value=0, case_sensitive=False, failure_mode="ne")
    assert (await m0.grade(_ctx({"n": False}))).label is GradeLabel.FAIL
    assert (await m0.grade(_ctx({"n": 0}))).label is GradeLabel.PASS


@pytest.mark.asyncio
async def test_root_reason_counts_whole_tree_including_skipped() -> None:
    # 3 levels; finder gates → resolver skipped. Reason must say 2/3, not 2/2.
    levels = [
        _Level(
            key="l1",
            extract="x",
            match=SpyGrader("l1", GradeLabel.FAIL),
            gate=True,
            children=[_Level(key="l2", extract="y", match=SpyGrader("l2", GradeLabel.PASS))],
        ),
        _Level(key="l3", extract="z", match=SpyGrader("l3", GradeLabel.PASS)),
    ]
    result = await FunnelGrader("f", levels=levels).grade(_ctx({}))
    assert result.details["levels"] == 3
    assert result.details["evaluated"] == 2  # l1 + l3; l2 skipped
    assert "2/3" in result.reason
