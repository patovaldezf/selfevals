"""LLMJudgeGrader: invoke an AgentAdapter as a judge.

The grader formats a rubric prompt with the case input, the agent's final
response, and the rubric instructions; the judge adapter returns a JSON
response with `label`, `reason`, optional `score`, optional `confidence`.

MVP ships single-judge. The constructor accepts an optional `card` (a
`GraderCard`) so future panel infra can pin behavior to a calibrated
configuration. When `card.blocking` and calibration metrics are below
thresholds, the grader degrades to advisory (returns SKIPPED) per the
operational spec — unless `force=True` is set.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from string import Template
from typing import TYPE_CHECKING, Any

from selfevals.graders.base import GradeLabel, Grader, GraderContext, GradeResult

if TYPE_CHECKING:
    from selfevals.runner.adapters import AdapterRequest, AgentAdapter
    from selfevals.schemas.grader_card import GraderCard


_DEFAULT_RUBRIC = """You are an evaluator. Read the case input and the agent's response,
then decide whether the response meets the rubric.

Rubric:
$rubric

Case input:
$case_input

Agent response:
$agent_response

Return a single JSON object with keys:
- label: one of "pass", "fail", "partial"
- reason: short justification
- score: number in [0, 1] (optional)
- confidence: number in [0, 1] (optional)
"""


@dataclass(frozen=True)
class RubricTemplate:
    rubric: str
    template: str = _DEFAULT_RUBRIC

    def render(self, *, case_input: Any, agent_response: str) -> str:
        return Template(self.template).safe_substitute(
            rubric=self.rubric,
            case_input=json.dumps(case_input, ensure_ascii=False),
            agent_response=agent_response,
        )


@dataclass(frozen=True)
class JudgeDecision:
    label: GradeLabel
    reason: str
    score: float | None = None
    confidence: float | None = None


def _parse_judge_output(text: str) -> JudgeDecision:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"judge did not return valid JSON: {exc}; text={text!r}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"judge JSON must be an object, got {type(data).__name__}")
    raw_label = str(data.get("label", "")).strip().lower()
    if raw_label not in {label.value for label in GradeLabel}:
        raise ValueError(f"judge returned unknown label: {raw_label!r}")
    label = GradeLabel(raw_label)
    reason = str(data.get("reason", "")).strip() or "no reason supplied"
    score = data.get("score")
    confidence = data.get("confidence")
    return JudgeDecision(
        label=label,
        reason=reason,
        score=float(score) if score is not None else None,
        confidence=float(confidence) if confidence is not None else None,
    )


def _is_card_calibrated(card: GraderCard | None) -> bool:
    if card is None:
        return True
    if not card.blocking:
        return True
    metrics = card.metrics
    thresholds = card.thresholds
    if thresholds.min_precision is not None and (
        metrics.precision is None or metrics.precision < thresholds.min_precision
    ):
        return False
    if thresholds.min_recall is not None and (
        metrics.recall is None or metrics.recall < thresholds.min_recall
    ):
        return False
    return not (
        thresholds.max_high_risk_false_negatives is not None
        and (
            metrics.high_risk_false_negatives is None
            or metrics.high_risk_false_negatives > thresholds.max_high_risk_false_negatives
        )
    )


class LLMJudgeGrader(Grader):
    def __init__(
        self,
        name: str,
        *,
        judge_adapter: AgentAdapter,
        rubric: RubricTemplate,
        card: GraderCard | None = None,
        force: bool = False,
    ) -> None:
        if not name:
            raise ValueError("grader name must be non-empty")
        self.name = name
        self._judge = judge_adapter
        self._rubric = rubric
        self._card = card
        self._force = force

    async def grade(self, context: GraderContext) -> GradeResult:
        if not self._force and not _is_card_calibrated(self._card):
            return GradeResult(
                grader=self.name,
                label=GradeLabel.SKIPPED,
                reason="blocking grader below calibration thresholds; degraded to advisory",
                score=None,
                confidence=None,
                details={"card_state": getattr(self._card, "state", None)},
            )
        prompt = self._rubric.render(
            case_input=context.case.input,
            agent_response=_extract_response_text(context),
        )
        request = _build_judge_request(context, prompt, self.name)
        try:
            response = await self._judge.invoke(request)
        except Exception as exc:
            return GradeResult(
                grader=self.name,
                label=GradeLabel.ERROR,
                reason=f"judge invocation failed: {exc}",
            )
        try:
            decision = _parse_judge_output(response.content or "")
        except ValueError as exc:
            return GradeResult(
                grader=self.name,
                label=GradeLabel.ERROR,
                reason=f"could not parse judge output: {exc}",
            )
        return GradeResult(
            grader=self.name,
            label=decision.label,
            reason=decision.reason,
            score=decision.score,
            confidence=decision.confidence,
        )


def _extract_response_text(context: GraderContext) -> str:
    if context.response is not None and context.response.content:
        return context.response.content
    return ""


def _build_judge_request(context: GraderContext, prompt: str, grader_name: str) -> AdapterRequest:
    from selfevals.runner.adapters import AdapterRequest  # local import to avoid cycle

    return AdapterRequest(
        workspace_id=context.case.workspace_id,
        case_id=context.case.id,
        input={"messages": [{"role": "user", "content": prompt}]},
        metadata={"grader": grader_name, "judge": True},
    )
