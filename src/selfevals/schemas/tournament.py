"""Tournament: the ranking produced by a batch of pairwise comparisons.

A tournament judges many pairs of candidates (via `runner/pairwise_tournament.py`)
and aggregates the verdicts into a total order with Elo or Bradley-Terry. The
verdicts themselves persist as `PairwiseVerdict`s; this entity records the
*result* — the ordered ranking, the strategy/method used, and the candidate set
— so it is queryable and auditable, and a future UI can render it.

Typed strictly (no opaque dicts) so it maps 1:1 to columns the day the repo
migrates pairwise off the generic `entities` table.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field

from selfevals.schemas._base import BaseEntity, NonEmptyStr, SelfEvalsModel

PairingStrategy = Literal["vs_baseline", "all_pairs", "sampled", "swiss"]
RankingMethod = Literal["elo", "bradley_terry"]


class TournamentRow(SelfEvalsModel):
    """One candidate's standing in the final ranking (sorted best-first)."""

    candidate_id: NonEmptyStr
    rank: int = Field(ge=1)
    score: float
    wins: int = Field(ge=0)
    losses: int = Field(ge=0)
    ties: int = Field(ge=0)
    n_comparisons: int = Field(ge=0)


class Tournament(BaseEntity):
    _id_prefix: ClassVar[str] = "tn"

    experiment_id: str | None = None
    strategy: PairingStrategy
    method: RankingMethod
    candidate_ids: list[NonEmptyStr]
    baseline_id: str | None = None
    """Set only for the `vs_baseline` strategy."""
    n_comparisons: int = Field(default=0, ge=0)
    swap_and_average: bool = False
    ranking: list[TournamentRow] = Field(default_factory=list)
