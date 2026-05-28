from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from selfevals.graders.base import (
    GradeLabel,
    Grader,
    GraderContext,
    GradeResult,
)
from selfevals.graders.judge_panel import (
    CounterfactualConfig,
    HumanSpotCheckConfig,
    JudgePanelGrader,
)
from selfevals.graders.llm_judge import LLMJudgeGrader, RubricTemplate
from selfevals.runner.adapters import AdapterRequest, AdapterResponse, EmbeddedAdapter
from selfevals.schemas.enums import (
    DatasetSource,
    DatasetType,
    GroundTruthMethod,
    Level,
    SandboxMode,
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
from selfevals.schemas.experiment import (
    CounterfactualSpec,
    HumanSpotCheckSpec,
    JudgeDefenses,
    JudgePanel,
)
from selfevals.schemas.trace import (
    AgentSnapshotRef,
    AgentTurnSpan,
    EnvironmentInfo,
    FinalState,
    RunInfo,
    Trace,
)

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"
T0 = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)


def _case(context: dict[str, object] | None = None) -> EvalCase:
    return EvalCase(
        id=EvalCase.make_id(),
        workspace_id=WS,
        name="t",
        task_type="x",
        input={"messages": [{"role": "user", "content": "hi"}]},
        context=context,
        taxonomy=CaseTaxonomy(
            level=Level.FINAL_RESPONSE,
            feature=FeatureTag(primary="commerce.product_resolution"),
            source=SourceInfo(type=DatasetSource.HANDCRAFTED),
            ground_truth=GroundTruthSpec(methods=[GroundTruthMethod.LLM_JUDGE]),
            dataset_type=DatasetType.CAPABILITY,
        ),
        expected=Expected(),
    )


def _trace() -> Trace:
    return Trace(
        id=Trace.make_id(),
        workspace_id=WS,
        run=RunInfo(run_id="run_01"),
        agent=AgentSnapshotRef(agent_id="ag_x", agent_version=1),
        environment=EnvironmentInfo(
            framework_version="selfevals/0.0.5",
            runtime="python-3.12",
            sandbox=SandboxMode.MOCK,
            started_at=T0,
        ),
        final_state=FinalState(status=TraceState.COMPLETED),
        spans=[AgentTurnSpan(id="sp_turn", name="t", started_at=T0)],
    )


def _ctx(content: str = "the answer", context: dict[str, object] | None = None) -> GraderContext:
    return GraderContext(
        case=_case(context),
        trace=_trace(),
        response=AdapterResponse(content=content),
    )


class FakeJudge(Grader):
    """Deterministic in-memory judge: returns a fixed verdict, no API key."""

    def __init__(
        self,
        name: str,
        *,
        label: GradeLabel,
        score: float | None = None,
        confidence: float | None = None,
    ) -> None:
        self.name = name
        self._label = label
        self._score = score
        self._confidence = confidence

    async def grade(self, context: GraderContext) -> GradeResult:
        return GradeResult(
            grader=self.name,
            label=self._label,
            reason=f"{self.name} says {self._label.value}",
            score=self._score,
            confidence=self._confidence,
        )


class ContentEchoJudge(Grader):
    """Verdict depends on the response text — used to drive counterfactuals.

    Passes when the (paraphrased) response text contains a target token,
    so paraphrase prefixes that do not strip the token keep the verdict
    stable. A flipping variant is produced by toggling sensitivity.
    """

    def __init__(self, name: str, *, sensitive: bool = False) -> None:
        self.name = name
        self._sensitive = sensitive

    async def grade(self, context: GraderContext) -> GradeResult:
        text = context.response.content if context.response is not None else ""
        text = text or ""
        if self._sensitive:
            # Any added prefix (non-empty paraphrase) flips this judge to fail.
            label = GradeLabel.PASS if text == "base" else GradeLabel.FAIL
        else:
            label = GradeLabel.PASS
        return GradeResult(grader=self.name, label=label, reason="echo", score=None)


# --- consensus rules -------------------------------------------------------


@pytest.mark.asyncio
async def test_majority_two_of_three_passes() -> None:
    judges = [
        FakeJudge("a", label=GradeLabel.PASS),
        FakeJudge("b", label=GradeLabel.PASS),
        FakeJudge("c", label=GradeLabel.FAIL),
    ]
    panel = JudgePanelGrader("panel", judges=judges, consensus_rule="majority")
    res = await panel.grade(_ctx())
    assert res.label == GradeLabel.PASS
    assert res.breakdown is not None
    assert res.breakdown.key == "judge_panel"
    # one child per judge (no counterfactual node when disabled)
    assert [c.key for c in res.breakdown.children] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_majority_tie_resolves_conservatively() -> None:
    judges = [
        FakeJudge("a", label=GradeLabel.PASS),
        FakeJudge("b", label=GradeLabel.FAIL),
    ]
    panel = JudgePanelGrader("panel", judges=judges, consensus_rule="majority")
    res = await panel.grade(_ctx())
    # 1-1 tie breaks toward the more conservative verdict.
    assert res.label == GradeLabel.FAIL


@pytest.mark.asyncio
async def test_unanimous_single_fail_flips_panel() -> None:
    judges = [
        FakeJudge("a", label=GradeLabel.PASS),
        FakeJudge("b", label=GradeLabel.PASS),
        FakeJudge("c", label=GradeLabel.FAIL),
    ]
    panel = JudgePanelGrader("panel", judges=judges, consensus_rule="unanimous")
    res = await panel.grade(_ctx())
    assert res.label == GradeLabel.FAIL


@pytest.mark.asyncio
async def test_unanimous_all_pass() -> None:
    judges = [
        FakeJudge("a", label=GradeLabel.PASS),
        FakeJudge("b", label=GradeLabel.PASS),
    ]
    panel = JudgePanelGrader("panel", judges=judges, consensus_rule="unanimous")
    res = await panel.grade(_ctx())
    assert res.label == GradeLabel.PASS


@pytest.mark.asyncio
async def test_weighted_pesos_decide() -> None:
    # Two fail votes but the single heavy pass outweighs them.
    judges = [
        FakeJudge("heavy", label=GradeLabel.PASS),
        FakeJudge("light1", label=GradeLabel.FAIL),
        FakeJudge("light2", label=GradeLabel.FAIL),
    ]
    panel = JudgePanelGrader(
        "panel",
        judges=judges,
        consensus_rule="weighted",
        weights=[5.0, 1.0, 1.0],
    )
    res = await panel.grade(_ctx())
    assert res.label == GradeLabel.PASS
    # weighted score is the weighted mean of label-implied scores
    assert res.score is not None
    assert res.score == pytest.approx((5 * 1.0 + 1 * 0.0 + 1 * 0.0) / 7)


@pytest.mark.asyncio
async def test_error_and_skipped_excluded_from_vote() -> None:
    judges = [
        FakeJudge("a", label=GradeLabel.PASS),
        FakeJudge("b", label=GradeLabel.ERROR),
        FakeJudge("c", label=GradeLabel.SKIPPED),
    ]
    panel = JudgePanelGrader("panel", judges=judges, consensus_rule="majority")
    res = await panel.grade(_ctx())
    # only judge a votes -> pass
    assert res.label == GradeLabel.PASS


@pytest.mark.asyncio
async def test_all_excluded_yields_error() -> None:
    judges = [
        FakeJudge("a", label=GradeLabel.ERROR),
        FakeJudge("b", label=GradeLabel.SKIPPED),
    ]
    panel = JudgePanelGrader("panel", judges=judges, consensus_rule="majority")
    res = await panel.grade(_ctx())
    assert res.label == GradeLabel.ERROR
    assert "errored or were skipped" in res.reason
    # breakdown still records every member
    assert res.breakdown is not None
    assert [c.key for c in res.breakdown.children] == ["a", "b"]


@pytest.mark.asyncio
async def test_member_raise_is_captured_as_error() -> None:
    class Boom(Grader):
        name = "boom"

        async def grade(self, context: GraderContext) -> GradeResult:
            raise RuntimeError("kaboom")

    judges = [FakeJudge("a", label=GradeLabel.PASS), Boom()]
    panel = JudgePanelGrader("panel", judges=judges, consensus_rule="majority")
    res = await panel.grade(_ctx())
    # boom excluded, a wins
    assert res.label == GradeLabel.PASS
    boom_child = next(c for c in res.breakdown.children if c.key == "boom")
    assert boom_child.label == GradeLabel.ERROR
    assert "kaboom" in boom_child.reason


# --- counterfactual variance ----------------------------------------------


@pytest.mark.asyncio
async def test_counterfactual_low_variance_keeps_confidence() -> None:
    # Stable judges -> zero variance -> confidence not degraded.
    judges = [FakeJudge("a", label=GradeLabel.PASS, confidence=0.8)]
    panel = JudgePanelGrader(
        "panel",
        judges=judges,
        consensus_rule="majority",
        counterfactuals=CounterfactualConfig(enabled=True, pairs_per_case=3),
    )
    res = await panel.grade(_ctx())
    assert res.label == GradeLabel.PASS
    assert res.confidence == pytest.approx(0.8)
    cf = res.details["counterfactual"]
    assert cf["variance"] == pytest.approx(0.0)
    assert cf["high_variance"] is False
    # trace links use the paraphrase_variant kind
    assert len(cf["trace_links"]) == 3
    assert all(link["kind"] == "paraphrase_variant" for link in cf["trace_links"])


@pytest.mark.asyncio
async def test_counterfactual_high_variance_is_advisory_does_not_flip() -> None:
    # Sensitive judge: base passes, all paraphrase variants fail -> high
    # variance. Label must NOT flip (base verdict authoritative); confidence
    # is degraded and an advisory weight=0 breakdown child is attached.
    judge = ContentEchoJudge("echo", sensitive=True)
    # base text == "base" so base verdict is PASS
    base_judge_confidence = FakeJudge("c", label=GradeLabel.PASS, confidence=0.9)
    panel_with_conf = JudgePanelGrader(
        "panel",
        judges=[base_judge_confidence, judge],
        consensus_rule="majority",
        counterfactuals=CounterfactualConfig(
            enabled=True, pairs_per_case=3, max_score_variance=0.01
        ),
    )
    ctx = _ctx(content="base")
    res = await panel_with_conf.grade(ctx)
    # base: both pass -> PASS authoritative
    assert res.label == GradeLabel.PASS
    cf = res.details["counterfactual"]
    assert cf["high_variance"] is True
    assert cf["variance"] > 0.01
    # confidence degraded (0.9 -> 0.45) but label intact
    assert res.confidence == pytest.approx(0.45)
    # advisory weight=0 counterfactual child present
    cf_child = next(c for c in res.breakdown.children if c.key == "counterfactual_variance")
    assert cf_child.weight == 0.0
    assert "judge_instability" in cf_child.failure_modes


# --- human spot-check ------------------------------------------------------


@pytest.mark.asyncio
async def test_spot_check_seeded_emits_annotation_when_selected() -> None:
    judges = [FakeJudge("a", label=GradeLabel.PASS)]
    # sample_rate=1.0 always selects; deterministic given the seed.
    panel = JudgePanelGrader(
        "panel",
        judges=judges,
        consensus_rule="majority",
        human_spot_check=HumanSpotCheckConfig(enabled=True, sample_rate=1.0),
        rng_seed=42,
        annotator_id="rev-1",
    )
    res = await panel.grade(_ctx())
    sc = res.details["human_spot_check"]
    assert sc["selected"] is True
    stub = sc["annotation_stub"]
    assert stub["annotator_id"] == "rev-1"
    assert stub["labels"]["data"]["panel_label"] == "pass"
    assert stub["flagged_for_adjudication"] is True
    # non-blocking: verdict unchanged
    assert res.label == GradeLabel.PASS


@pytest.mark.asyncio
async def test_spot_check_not_selected_when_rate_zero() -> None:
    judges = [FakeJudge("a", label=GradeLabel.PASS)]
    panel = JudgePanelGrader(
        "panel",
        judges=judges,
        consensus_rule="majority",
        human_spot_check=HumanSpotCheckConfig(enabled=True, sample_rate=0.0),
    )
    res = await panel.grade(_ctx())
    sc = res.details["human_spot_check"]
    assert sc["selected"] is False
    assert "annotation_stub" not in sc


# --- calibration -----------------------------------------------------------


@pytest.mark.asyncio
async def test_calibration_advisory_when_human_label_reachable() -> None:
    judges = [FakeJudge("a", label=GradeLabel.PASS)]
    panel = JudgePanelGrader("panel", judges=judges, consensus_rule="majority")
    res = await panel.grade(_ctx(context={"human_label": "pass"}))
    cal = res.details["calibration"]
    assert cal["advisory"] is True
    assert cal["human_label"] == "pass"
    assert cal["panel_label"] == "pass"
    assert cal["agreement"] is True
    assert cal["accuracy"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_calibration_absent_without_human_label() -> None:
    judges = [FakeJudge("a", label=GradeLabel.PASS)]
    panel = JudgePanelGrader("panel", judges=judges, consensus_rule="majority")
    res = await panel.grade(_ctx())
    assert "calibration" not in res.details


# --- validation ------------------------------------------------------------


def test_empty_name_rejected() -> None:
    with pytest.raises(ValueError, match="name must be non-empty"):
        JudgePanelGrader("", judges=[FakeJudge("a", label=GradeLabel.PASS)])


def test_empty_panel_rejected() -> None:
    with pytest.raises(ValueError, match="at least one judge"):
        JudgePanelGrader("p", judges=[])


def test_unknown_consensus_rule_rejected() -> None:
    with pytest.raises(ValueError, match="consensus_rule must be one of"):
        JudgePanelGrader(
            "p", judges=[FakeJudge("a", label=GradeLabel.PASS)], consensus_rule="plurality"
        )


def test_weighted_requires_weights() -> None:
    with pytest.raises(ValueError, match="requires weights"):
        JudgePanelGrader(
            "p", judges=[FakeJudge("a", label=GradeLabel.PASS)], consensus_rule="weighted"
        )


def test_weights_length_mismatch_rejected() -> None:
    with pytest.raises(ValueError, match="must match judges length"):
        JudgePanelGrader(
            "p",
            judges=[FakeJudge("a", label=GradeLabel.PASS)],
            weights=[1.0, 2.0],
        )


def test_negative_weights_rejected() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        JudgePanelGrader(
            "p",
            judges=[FakeJudge("a", label=GradeLabel.PASS)],
            consensus_rule="weighted",
            weights=[-1.0],
        )


def test_duplicate_judge_names_rejected() -> None:
    with pytest.raises(ValueError, match="unique"):
        JudgePanelGrader(
            "p",
            judges=[
                FakeJudge("dup", label=GradeLabel.PASS),
                FakeJudge("dup", label=GradeLabel.FAIL),
            ],
        )


# --- from_defenses ---------------------------------------------------------


@pytest.mark.asyncio
async def test_from_defenses_consumes_schema_fields() -> None:
    defenses = JudgeDefenses(
        panel=JudgePanel(members=["a", "b"], consensus_rule="unanimous"),
        counterfactuals=CounterfactualSpec(enabled=True, pairs_per_case=2),
        human_spot_check=HumanSpotCheckSpec(enabled=True, sample_rate=1.0),
    )
    judges = [
        FakeJudge("a", label=GradeLabel.PASS),
        FakeJudge("b", label=GradeLabel.FAIL),
    ]
    panel = JudgePanelGrader.from_defenses("panel", judges=judges, defenses=defenses)
    res = await panel.grade(_ctx())
    # unanimous rule wired from the schema -> single fail flips
    assert res.label == GradeLabel.FAIL
    # counterfactual + spot-check enabled from the schema
    assert "counterfactual" in res.details
    assert res.details["counterfactual"]["pairs_per_case"] == 2
    assert res.details["human_spot_check"]["selected"] is True


# --- real LLMJudgeGrader over EmbeddedAdapter (reuse + gather) -------------


def _embedded_judge(name: str, payload: dict[str, object]) -> LLMJudgeGrader:
    def fn(_: AdapterRequest) -> AdapterResponse:
        return AdapterResponse(content=json.dumps(payload))

    return LLMJudgeGrader(
        name,
        judge_adapter=EmbeddedAdapter(fn),
        rubric=RubricTemplate(rubric="Did the agent answer correctly?"),
    )


@pytest.mark.asyncio
async def test_panel_reuses_real_llm_judges_via_gather() -> None:
    judges: list[Grader] = [
        _embedded_judge("j1", {"label": "pass", "reason": "ok", "score": 0.9}),
        _embedded_judge("j2", {"label": "pass", "reason": "ok", "score": 0.8}),
        _embedded_judge("j3", {"label": "fail", "reason": "no", "score": 0.1}),
    ]
    panel = JudgePanelGrader("panel", judges=judges, consensus_rule="majority")
    res = await panel.grade(_ctx())
    assert res.label == GradeLabel.PASS
    # weighted mean of explicit member scores (equal weights for majority)
    assert res.score == pytest.approx((0.9 + 0.8 + 0.1) / 3)
    # one breakdown child per real judge, carrying member scores
    assert {c.key for c in res.breakdown.children} == {"j1", "j2", "j3"}
    j1 = next(c for c in res.breakdown.children if c.key == "j1")
    assert j1.score == 0.9
