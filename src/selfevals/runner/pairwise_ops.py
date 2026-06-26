"""Storage-facing operations for pairwise verdicts.

This is the ONLY module that touches storage for the pairwise feature — the
migration cut-point. When the repo moves pairwise verdicts off the generic
`entities` table onto a dedicated, typed SQL table, only the bodies of these
functions change; the grader and the API layer are unaffected.

Three operations:

* `ingest_verdicts(scope, verdicts)` — validate referenced ids exist (fail-fast,
  the way `analysis/ingest.py` validates before writing), then persist. There are
  no SQL foreign keys (the referents live in the generic blob), so referential
  integrity is enforced here, in code.
* `list_verdicts(scope, ...)` — list a workspace's verdicts, filtered by
  experiment / case / judge_kind.
* `compute_calibration(scope, ...)` — LLM-vs-human agreement over the *same*
  pairs, broken down by rubric_version. This is what powers judge calibration
  and prompt refinement: where the LLM disagrees with humans is where the rubric
  needs work.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from selfevals.schemas.eval_case import EvalCase
from selfevals.schemas.experiment import Experiment
from selfevals.schemas.pairwise_verdict import PairwiseVerdict
from selfevals.schemas.trace import Trace
from selfevals.storage.interface import ListFilter, WorkspaceScope


class PairwiseOpError(ValueError):
    """A pairwise operation failed validation (e.g. a referenced id is unknown)."""


def _pair_key(verdict: PairwiseVerdict) -> tuple[str, str]:
    """Identity of the compared pair, order-independent.

    Two verdicts judge "the same pair" when both sides reference the same
    underlying output. We key on (trace_id or content_snapshot) per side and sort
    so A/B order (and swap position) doesn't split a pair.
    """

    def side(ref: object) -> str:
        r = ref
        # `r` is a PairRef; prefer the stable trace id, fall back to the frozen
        # snapshot so arbitrary/reference sides (no trace) still pair up.
        return getattr(r, "trace_id", None) or getattr(r, "content_snapshot", None) or ""

    a = side(verdict.a_ref)
    b = side(verdict.b_ref)
    return (a, b) if a <= b else (b, a)


def ingest_verdicts(
    scope: WorkspaceScope,
    verdicts: list[PairwiseVerdict],
    *,
    validate_refs: bool = True,
) -> list[PairwiseVerdict]:
    """Validate and persist a batch of verdicts. Returns what was written.

    When `validate_refs` is set (default), every non-null `experiment_id` /
    `case_id` / side `trace_id` must resolve to an existing entity in the scope;
    the first miss raises `PairwiseOpError` and nothing is written (validate the
    whole batch before any write — fail-fast, like `analysis/ingest.py`).
    """
    if validate_refs:
        for v in verdicts:
            _validate_refs(scope, v)
    for v in verdicts:
        scope.assert_owns(v)
        scope.put_entity(v)
    return verdicts


def _validate_refs(scope: WorkspaceScope, verdict: PairwiseVerdict) -> None:
    if verdict.experiment_id is not None and not scope.exists(Experiment, verdict.experiment_id):
        raise PairwiseOpError(f"unknown experiment_id: {verdict.experiment_id!r}")
    if verdict.case_id is not None and not scope.exists(EvalCase, verdict.case_id):
        raise PairwiseOpError(f"unknown case_id: {verdict.case_id!r}")
    for label, ref in (("a_ref", verdict.a_ref), ("b_ref", verdict.b_ref)):
        if ref.trace_id is not None and not scope.exists(Trace, ref.trace_id):
            raise PairwiseOpError(f"unknown {label}.trace_id: {ref.trace_id!r}")


def list_verdicts(
    scope: WorkspaceScope,
    *,
    experiment_id: str | None = None,
    case_id: str | None = None,
    judge_kind: str | None = None,
) -> list[PairwiseVerdict]:
    """List verdicts in this workspace, AND-ing the provided filters."""
    where: dict[str, object] = {}
    if experiment_id is not None:
        where["experiment_id"] = experiment_id
    if case_id is not None:
        where["case_id"] = case_id
    if judge_kind is not None:
        where["judge_kind"] = judge_kind
    entities = scope.list_entities(PairwiseVerdict, ListFilter(where=where))
    return [e for e in entities if isinstance(e, PairwiseVerdict)]


@dataclass(frozen=True)
class CalibrationCell:
    """LLM-vs-human agreement for one rubric_version."""

    rubric_version: int | None
    compared_pairs: int
    agreements: int
    disagreements: int

    @property
    def agreement_rate(self) -> float:
        total = self.agreements + self.disagreements
        return self.agreements / total if total else 0.0


@dataclass(frozen=True)
class CalibrationReport:
    """Agreement between the LLM judge and human judges over the same pairs."""

    compared_pairs: int
    agreements: int
    disagreements: int
    by_rubric_version: list[CalibrationCell] = field(default_factory=list)

    @property
    def agreement_rate(self) -> float:
        total = self.agreements + self.disagreements
        return self.agreements / total if total else 0.0


def compute_calibration(
    scope: WorkspaceScope,
    *,
    experiment_id: str | None = None,
) -> CalibrationReport:
    """Compare LLM vs human verdicts on the same pairs.

    For every pair judged by *both* an LLM and a human, count how often they
    agree on `preferred`. Aggregated overall and per `rubric_version` so a
    prompt-refinement loop can see which rubric version improved agreement.
    Pairs with only one judge_kind are ignored (nothing to compare).
    """
    verdicts = list_verdicts(scope, experiment_id=experiment_id)

    # pair -> rubric_version -> {"llm": preferred, "human": preferred}
    by_pair: dict[tuple[str, str], dict[int | None, dict[str, str]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for v in verdicts:
        by_pair[_pair_key(v)][v.rubric_version][v.judge_kind] = v.preferred

    per_version_agree: dict[int | None, int] = defaultdict(int)
    per_version_disagree: dict[int | None, int] = defaultdict(int)
    for versions in by_pair.values():
        for rubric_version, judged in versions.items():
            if "llm" not in judged or "human" not in judged:
                continue
            if judged["llm"] == judged["human"]:
                per_version_agree[rubric_version] += 1
            else:
                per_version_disagree[rubric_version] += 1

    cells = [
        CalibrationCell(
            rubric_version=rv,
            compared_pairs=per_version_agree[rv] + per_version_disagree[rv],
            agreements=per_version_agree[rv],
            disagreements=per_version_disagree[rv],
        )
        for rv in sorted(
            set(per_version_agree) | set(per_version_disagree),
            key=lambda x: (x is None, x),
        )
    ]
    total_agree = sum(per_version_agree.values())
    total_disagree = sum(per_version_disagree.values())
    return CalibrationReport(
        compared_pairs=total_agree + total_disagree,
        agreements=total_agree,
        disagreements=total_disagree,
        by_rubric_version=cells,
    )
