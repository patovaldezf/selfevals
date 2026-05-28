"""Graders: score traces against expectations.

A `Grader` reads a Trace + EvalCase and returns a `GradeResult` (label
+ optional score + reason). Concrete graders that ship:

- `DeterministicGrader` evaluates rule-based expectations declared on
  `EvalCase.expected` (must_include / forbidden_tools / regex / schema).
- `GuardrailGrader` enforces deterministic content guardrails
  (forbidden/required regex, basic PII, double-value) and folds in
  failed `GuardrailCheckSpan` entries from the trace. FAIL is blocking.
- `LLMJudgeGrader` invokes an `AgentAdapter` as a judge against a rubric
  prompt; single-judge today, panel infrastructure-ready.
- `ArtifactCompletenessGrader` scores artifact-producing agents (structured
  documents/reports) on schema validity + required-section presence, with an
  optional advisory LLM-judge quality signal that never flips the verdict.
- `JudgePanelGrader` composes N judges (typically `LLMJudgeGrader`) into one
  authoritative verdict via a consensus rule and wires up the judge-defense
  levers (panel / counterfactual variance / human spot-check). It needs
  constructor args (the judge list), so it is exposed only here for
  programmatic construction, not via the zero-arg `registry`.

Calibration helpers turn observed predictions + human annotations into
the metrics tracked on a `GraderCard`.
"""

from selfevals.graders.artifact import ArtifactCompletenessGrader
from selfevals.graders.base import (
    BreakdownNode,
    GradeLabel,
    Grader,
    GraderContext,
    GradeResult,
)
from selfevals.graders.calibration import (
    CalibrationReport,
    HumanLabel,
    PredictedLabel,
    compute_classification_metrics,
)
from selfevals.graders.deterministic import (
    DeterministicGrader,
    DeterministicRuleViolationError,
)
from selfevals.graders.guardrail import GuardrailGrader
from selfevals.graders.judge_panel import (
    CounterfactualConfig,
    HumanSpotCheckConfig,
    JudgePanelGrader,
)
from selfevals.graders.llm_judge import (
    JudgeDecision,
    LLMJudgeGrader,
    RubricTemplate,
)
from selfevals.graders.trajectory import (
    HardInvariants,
    TrajectoryGrader,
)

__all__ = [
    "ArtifactCompletenessGrader",
    "BreakdownNode",
    "CalibrationReport",
    "CounterfactualConfig",
    "DeterministicGrader",
    "DeterministicRuleViolationError",
    "GradeLabel",
    "GradeResult",
    "Grader",
    "GraderContext",
    "GuardrailGrader",
    "HardInvariants",
    "HumanLabel",
    "HumanSpotCheckConfig",
    "JudgeDecision",
    "JudgePanelGrader",
    "LLMJudgeGrader",
    "PredictedLabel",
    "RubricTemplate",
    "TrajectoryGrader",
    "compute_classification_metrics",
]
