"""Grader ABC + shared result types.

The grader contract is intentionally narrow: receive a context bundle
(case, trace, optional response) and return a `GradeResult`. The case
and the trace carry everything needed to score; graders never reach
into storage themselves.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from selfevals.runner.adapters import AdapterResponse
    from selfevals.schemas.eval_case import EvalCase
    from selfevals.schemas.trace import Trace


class GradeLabel(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    PARTIAL = "partial"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class GradeResult:
    grader: str
    label: GradeLabel
    reason: str
    score: float | None = None
    confidence: float | None = None
    failure_modes: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GraderContext:
    case: EvalCase
    trace: Trace
    response: AdapterResponse | None = None


class Grader(ABC):
    name: str
    """Stable identifier — used as the GradeResult.grader and the
    Trace.grader_results[i].grader field."""

    @abstractmethod
    async def grade(self, context: GraderContext) -> GradeResult: ...
