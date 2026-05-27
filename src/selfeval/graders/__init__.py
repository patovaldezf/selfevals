"""Graders: score traces against expectations.

A `Grader` reads a Trace + EvalCase and returns a `GradeResult` (label
+ optional score + reason). Two concrete graders ship in MVP:

- `DeterministicGrader` evaluates rule-based expectations declared on
  `EvalCase.expected` (must_include / forbidden_tools / regex / schema).
- `LLMJudgeGrader` invokes an `AgentAdapter` as a judge against a rubric
  prompt; single-judge in MVP, panel infrastructure-ready for post-MVP.

Calibration helpers turn observed predictions + human annotations into
the metrics tracked on a `GraderCard`.
"""

from selfeval.graders.base import GradeLabel, Grader, GraderContext, GradeResult
from selfeval.graders.calibration import (
    CalibrationReport,
    HumanLabel,
    PredictedLabel,
    compute_classification_metrics,
)
from selfeval.graders.deterministic import (
    DeterministicGrader,
    DeterministicRuleViolationError,
)
from selfeval.graders.llm_judge import (
    JudgeDecision,
    LLMJudgeGrader,
    RubricTemplate,
)

__all__ = [
    "CalibrationReport",
    "DeterministicGrader",
    "DeterministicRuleViolationError",
    "GradeLabel",
    "GradeResult",
    "Grader",
    "GraderContext",
    "HumanLabel",
    "JudgeDecision",
    "LLMJudgeGrader",
    "PredictedLabel",
    "RubricTemplate",
    "compute_classification_metrics",
]
