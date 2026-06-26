"""PairwiseGrader: judge the agent's output (A) against a reference (B).

Unlike the point-wise graders ("is this output good?"), this grader asks a
head-to-head question ("is A or B better, and why?") — the *taste*/preference
axis. The MVP ships the `reference` mode: B is the case's authored
`reference_output` (a gold/taste answer). Because B is a field on the case (not
another live agent output), this fits the point-wise `Grader.grade(ctx)` contract
without touching the optimization loop — the other comparison origins
(`previous_iteration`, `variant`) need a second live output threaded through the
loop and are out of scope here.

The judge is an LLM, reusing the `llm_judge` scaffolding (`_build_judge_request`,
`_extract_response_text`, the `RubricTemplate` render pattern). The judge returns
`{preferred: "a"|"b"|"tie", margin: 0..1, reason}`, which we map onto the
point-wise `GradeResult` so the aggregator stays unchanged:

- `a` (agent wins)  -> PASS, score = 0.5 + margin/2
- `tie`             -> PASS if `tie_is_pass` (default), else FAIL; score 0.5
- `b` (reference wins) -> FAIL, score = 0.5 - margin/2, failure_mode
  `worse_than_reference`

`reference_output` absent -> SKIPPED. Invalid judge JSON -> ERROR (same as
`llm_judge`). Position bias is mitigated by `swap_and_average` (off by default):
run A/B and B/A, average the signed margin so a position-flipping judge nets out.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from string import Template
from typing import TYPE_CHECKING, Any, Literal

from selfevals.graders.base import GradeLabel, Grader, GraderContext, GradeResult
from selfevals.graders.llm_judge import _extract_response_text

if TYPE_CHECKING:
    from selfevals.runner.adapters import AgentAdapter

CompareAgainst = Literal["reference"]
"""MVP supports only `reference`. `previous_iteration`/`variant` are future work
(they need a second live output threaded through the loop)."""

FM_WORSE_THAN_REFERENCE = "worse_than_reference"


_DEFAULT_RUBRIC = """You are comparing two responses to the same task and \
deciding which one is better.

Rubric / what "better" means here:
$rubric

Task input:
$case_input

Response A:
$response_a

Response B:
$response_b

Return a single JSON object with keys:
- preferred: one of "a", "b", "tie"
- margin: number in [0, 1] — how decisive the preference is (0 for a tie)
- reason: short justification
"""


@dataclass(frozen=True)
class PairwiseRubric:
    rubric: str
    template: str = _DEFAULT_RUBRIC

    def render(self, *, case_input: Any, response_a: str, response_b: str) -> str:
        return Template(self.template).safe_substitute(
            rubric=self.rubric,
            case_input=json.dumps(case_input, ensure_ascii=False),
            response_a=response_a,
            response_b=response_b,
        )


@dataclass(frozen=True)
class PairwiseDecision:
    preferred: Literal["a", "b", "tie"]
    margin: float
    reason: str


def _parse_pairwise_output(text: str) -> PairwiseDecision:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"judge did not return valid JSON: {exc}; text={text!r}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"judge JSON must be an object, got {type(data).__name__}")
    raw = str(data.get("preferred", "")).strip().lower()
    if raw not in {"a", "b", "tie"}:
        raise ValueError(f"judge returned unknown `preferred`: {raw!r}")
    preferred: Literal["a", "b", "tie"] = raw  # type: ignore[assignment]
    margin_raw = data.get("margin", 0.0)
    try:
        margin = float(margin_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"judge `margin` is not a number: {margin_raw!r}") from exc
    margin = 0.0 if preferred == "tie" else max(0.0, min(1.0, margin))
    reason = str(data.get("reason", "")).strip() or "no reason supplied"
    return PairwiseDecision(preferred=preferred, margin=margin, reason=reason)


async def judge_pair(
    judge_adapter: AgentAdapter,
    rubric: PairwiseRubric,
    *,
    case_input: Any,
    response_a: str,
    response_b: str,
    workspace_id: str,
    case_id: str,
    grader_name: str,
    swap_and_average: bool = False,
) -> PairwiseDecision:
    """Judge A vs B once (or twice, swapped+averaged) — the shared judging core.

    Both `PairwiseGrader` (A=agent output, B=reference) and the tournament
    orchestrator (A/B = two candidates) call this so the comparative prompt and
    JSON parsing live in one place. The returned `preferred` is always relative
    to the real A. Raises `ValueError` on unparseable judge output; adapter
    exceptions propagate to the caller (which maps them to ERROR / drops the pair).
    """
    decision = await _invoke_judge(
        judge_adapter,
        rubric,
        case_input=case_input,
        response_a=response_a,
        response_b=response_b,
        workspace_id=workspace_id,
        case_id=case_id,
        grader_name=grader_name,
        swapped=False,
    )
    if swap_and_average:
        swapped = await _invoke_judge(
            judge_adapter,
            rubric,
            case_input=case_input,
            response_a=response_a,
            response_b=response_b,
            workspace_id=workspace_id,
            case_id=case_id,
            grader_name=grader_name,
            swapped=True,
        )
        decision = _average_decisions(decision, swapped)
    return decision


async def _invoke_judge(
    judge_adapter: AgentAdapter,
    rubric: PairwiseRubric,
    *,
    case_input: Any,
    response_a: str,
    response_b: str,
    workspace_id: str,
    case_id: str,
    grader_name: str,
    swapped: bool,
) -> PairwiseDecision:
    """One judge call. When `swapped`, show B as A and A as B, then flip the
    verdict back so `preferred` is always relative to the real A."""
    from selfevals.runner.adapters import AdapterRequest  # local import to avoid cycle

    first, second = (response_b, response_a) if swapped else (response_a, response_b)
    prompt = rubric.render(case_input=case_input, response_a=first, response_b=second)
    request = AdapterRequest(
        workspace_id=workspace_id,
        case_id=case_id,
        input={"messages": [{"role": "user", "content": prompt}]},
        metadata={"grader": grader_name, "judge": True, "pairwise": True},
    )
    response = await judge_adapter.invoke(request)
    decision = _parse_pairwise_output(response.content or "")
    return _unswap(decision) if swapped else decision


class PairwiseGrader(Grader):
    """LLM head-to-head judge: agent output (A) vs reference (B)."""

    def __init__(
        self,
        name: str,
        *,
        judge_adapter: AgentAdapter,
        rubric: PairwiseRubric,
        compare_against: CompareAgainst = "reference",
        tie_is_pass: bool = True,
        swap_and_average: bool = False,
    ) -> None:
        if not name:
            raise ValueError("grader name must be non-empty")
        self.name = name
        self._judge = judge_adapter
        self._rubric = rubric
        self._compare_against = compare_against
        self._tie_is_pass = tie_is_pass
        self._swap = swap_and_average

    async def grade(self, context: GraderContext) -> GradeResult:
        reference = context.case.reference_output
        if reference is None:
            return GradeResult(
                grader=self.name,
                label=GradeLabel.SKIPPED,
                reason="no reference_output on case; pairwise (reference) skipped",
            )
        response_a = _extract_response_text(context)

        try:
            decision = await judge_pair(
                self._judge,
                self._rubric,
                case_input=context.case.input,
                response_a=response_a,
                response_b=reference,
                workspace_id=context.case.workspace_id,
                case_id=context.case.id,
                grader_name=self.name,
                swap_and_average=self._swap,
            )
        except ValueError as exc:
            return GradeResult(
                grader=self.name,
                label=GradeLabel.ERROR,
                reason=f"could not parse judge output: {exc}",
            )
        except Exception as exc:  # adapter invocation failure, mirrors llm_judge
            return GradeResult(
                grader=self.name,
                label=GradeLabel.ERROR,
                reason=f"judge invocation failed: {exc}",
            )

        return self._to_grade_result(decision)

    def _to_grade_result(self, decision: PairwiseDecision) -> GradeResult:
        if decision.preferred == "a":
            return GradeResult(
                grader=self.name,
                label=GradeLabel.PASS,
                reason=decision.reason,
                score=0.5 + decision.margin / 2,
                details={"preferred": "a", "margin": decision.margin},
            )
        if decision.preferred == "tie":
            return GradeResult(
                grader=self.name,
                label=GradeLabel.PASS if self._tie_is_pass else GradeLabel.FAIL,
                reason=decision.reason,
                score=0.5,
                details={"preferred": "tie", "margin": 0.0},
            )
        return GradeResult(
            grader=self.name,
            label=GradeLabel.FAIL,
            reason=decision.reason,
            score=0.5 - decision.margin / 2,
            failure_modes=[FM_WORSE_THAN_REFERENCE],
            details={"preferred": "b", "margin": decision.margin},
        )


def _signed_margin(decision: PairwiseDecision) -> float:
    """Map a decision onto a signed axis where +margin favors A, -margin favors B."""
    if decision.preferred == "a":
        return decision.margin
    if decision.preferred == "b":
        return -decision.margin
    return 0.0


def _unswap(decision: PairwiseDecision) -> PairwiseDecision:
    """Flip a verdict produced with A/B swapped back to real-A orientation."""
    if decision.preferred == "a":
        return PairwiseDecision("b", decision.margin, decision.reason)
    if decision.preferred == "b":
        return PairwiseDecision("a", decision.margin, decision.reason)
    return decision


def _average_decisions(first: PairwiseDecision, second: PairwiseDecision) -> PairwiseDecision:
    """Average two real-A-oriented decisions into one (position-bias mitigation)."""
    avg = (_signed_margin(first) + _signed_margin(second)) / 2
    reason = f"swap-averaged: {first.reason} | {second.reason}"
    if abs(avg) < 1e-9:
        return PairwiseDecision("tie", 0.0, reason)
    if avg > 0:
        return PairwiseDecision("a", avg, reason)
    return PairwiseDecision("b", -avg, reason)
