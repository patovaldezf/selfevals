"""AnalysisStagingRecord — selfeval's advisory "this run is worth coding" marker.

When an experiment opts into error analysis (`error_analysis.enabled`) and an
iteration's fail rate clears the configured trigger, the loop persists one of
these. It records *that the trigger fired* — the experiment, the iteration, the
observed fail rate, and a human-readable reason — so a human or scheduler knows
an `analyze pull` is worth doing.

selfeval never invokes an agent or an LLM off the back of this. Staging is a
signal, not an action: `analyze pull` stays a pure read you can run anytime; the
marker just tells you *when it pays off*. See docs/spec/error_analysis_design.md §9.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import Field

from selfeval.schemas._base import BaseEntity, NonEmptyStr


class AnalysisStagingRecord(BaseEntity):
    _id_prefix: ClassVar[str] = "stg"

    experiment_id: NonEmptyStr
    iteration: int = Field(ge=0)
    fail_rate: float = Field(ge=0.0, le=1.0)
    threshold: float = Field(ge=0.0, le=1.0)
    scope: str
    """`failed_only` or `all` — what `analyze pull` should bundle for this run."""
    reason: NonEmptyStr
    consumed: bool = False
    """Set once an `analyze pull` has acted on this staging, so it isn't re-flagged."""
