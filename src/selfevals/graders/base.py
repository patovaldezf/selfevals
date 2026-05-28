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
class BreakdownNode:
    """One node in a grader's funnel breakdown tree.

    A breakdown decomposes a single grade into a weighted, recursive tree of
    sub-scores keyed by a stable `key`. It is purely informational: the
    top-level `GradeResult.label`/`score` remain authoritative for pass/fail.
    The breakdown drives the funnel drill-down (aggregator rollup, reporter
    sections, frontend) without ever flipping the verdict.

    Fields:
    - `key`: stable identity used to roll up the same node across cases. The
      aggregator groups by this, so it must be stable across repetitions.
    - `label`: optional per-node verdict (a `GradeLabel`); `None` for nodes
      that only carry a numeric score.
    - `score`: optional sub-score in [0, 1]; `None` for label-only nodes.
    - `weight`: relative contribution of this node among its siblings. A
      node with `weight=0` is advisory (e.g. diagnostic trajectory signals
      that must never affect the score).
    - `reason`: free-text rationale for this node.
    - `failure_modes`: stable failure-mode tags attributed to this node.
    - `children`: nested sub-nodes, forming the recursive funnel.

    The structure is JSON-serializable via `to_dict` / `from_dict` so it can
    ride along on the persistible `GraderResult.breakdown`.
    """

    key: str
    label: GradeLabel | None = None
    score: float | None = None
    weight: float = 1.0
    reason: str = ""
    failure_modes: list[str] = field(default_factory=list)
    children: list[BreakdownNode] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict (recurses into children)."""
        return {
            "key": self.key,
            "label": self.label.value if self.label is not None else None,
            "score": self.score,
            "weight": self.weight,
            "reason": self.reason,
            "failure_modes": list(self.failure_modes),
            "children": [child.to_dict() for child in self.children],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BreakdownNode:
        """Rebuild a node (and its subtree) from a `to_dict` payload."""
        raw_label = data.get("label")
        return cls(
            key=data["key"],
            label=GradeLabel(raw_label) if raw_label is not None else None,
            score=data.get("score"),
            weight=data.get("weight", 1.0),
            reason=data.get("reason", ""),
            failure_modes=list(data.get("failure_modes", [])),
            children=[cls.from_dict(child) for child in data.get("children", [])],
        )


@dataclass(frozen=True)
class GradeResult:
    grader: str
    label: GradeLabel
    reason: str
    score: float | None = None
    confidence: float | None = None
    failure_modes: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    breakdown: BreakdownNode | None = None
    """Optional funnel breakdown for this grade. Additive: `label`/`score`
    above stay authoritative for pass/fail; the breakdown is funnel
    information only (rolled up by the aggregator, rendered by the reporter,
    consumed by the frontend drill-down)."""


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
