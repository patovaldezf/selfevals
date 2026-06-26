"""PairwiseVerdict: a head-to-head judgement of output A vs output B.

Unlike point-wise graders ("is this output good?"), a pairwise verdict answers
"is A or B better, and why?". The same comparison can be judged by an LLM
(`judge_kind="llm"`, automatic, scales) or by a human via the web UI
(`judge_kind="human"`). With both judging the *same pairs* we unlock three uses:
collecting RLHF preferences, calibrating the LLM judge (LLM-vs-human agreement),
and refining the judge prompt by iterating over disagreements.

Storage note: this entity currently persists in the generic `entities` table,
but every field is concretely typed (no opaque `dict[str, Any]`) so it maps 1:1
to columns the day it migrates to a dedicated SQL table. Sub-objects (`PairRef`)
are explicit nested models; in a dedicated table `PairRef` flattens to `a_*` /
`b_*` columns or a child table.

The A/B IDs are plain typed columns — not SQL foreign keys, since the referenced
entities live in the generic blob. Existence is validated in the ingest layer
(`runner/pairwise_ops.py`), exactly as `analysis/ingest.py` does.
"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar, Literal

from pydantic import Field, model_validator

from selfevals.schemas._base import BaseEntity, NonEmptyStr, SelfEvalsModel

PairKind = Literal["agent_output", "reference", "iteration", "arbitrary"]
"""Where one side of the pair came from.

- `agent_output`: a live agent run for a case.
- `reference`: a gold/taste reference output (`EvalCase.reference_output`).
- `iteration`: the same case's output from another optimization iteration.
- `arbitrary`: any persisted output, paired for annotation off the loop.
"""

Preferred = Literal["a", "b", "tie"]
JudgeKind = Literal["llm", "human"]
Position = Literal["ab", "ba"]
"""Order the pair was shown to the judge in — `ba` means A/B were swapped.

Recorded so position-bias mitigation (`swap_and_average`) is auditable.
"""


class PairRef(SelfEvalsModel):
    """One side (A or B) of a pairwise comparison.

    Neutral to the pair's origin via `kind`. All id fields are optional because
    different origins populate different ones (a `reference` side has no
    `trace_id`; an `arbitrary` side may carry only a `content_snapshot`).

    `content_snapshot` freezes the judged text so the verdict stays auditable
    even if the underlying trace later changes or expires — critical for RLHF
    datasets, where the verdict must outlive the run that produced it.
    """

    kind: PairKind
    trace_id: str | None = None
    case_id: str | None = None
    iteration_id: str | None = None
    content_snapshot: str | None = None


class PairwiseVerdict(BaseEntity):
    """A single A-vs-B judgement, emitted by an LLM or a human."""

    _id_prefix: ClassVar[str] = "pv"

    # --- the pair ----------------------------------------------------------
    a_ref: PairRef
    b_ref: PairRef

    # --- the verdict -------------------------------------------------------
    preferred: Preferred
    margin: float = Field(default=0.0, ge=0.0, le=1.0)
    """Strength of preference in [0, 1]. 0 for a tie, larger = more decisive."""
    rationale: str | None = None

    # --- the judge ---------------------------------------------------------
    judge_kind: JudgeKind
    judge_id: NonEmptyStr
    """`"llm:<model>"` or `"human:<email>"` — stable identity of the judge."""
    judge_model: str | None = None
    rubric_version: int | None = Field(default=None, ge=1)
    position: Position | None = None

    # --- context (typed ids, no FK; validated at ingest) -------------------
    experiment_id: str | None = None
    case_id: str | None = None
    dataset_id: str | None = None

    # --- temporal (for the human UI path) ----------------------------------
    submitted_at: datetime | None = None
    duration_seconds: float | None = Field(default=None, ge=0.0)

    @model_validator(mode="after")
    def _tie_has_no_margin(self) -> PairwiseVerdict:
        if self.preferred == "tie" and self.margin != 0.0:
            raise ValueError("a tie must have margin 0.0")
        return self
