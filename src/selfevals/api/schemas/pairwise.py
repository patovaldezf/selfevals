"""Pairwise verdicts (LLM + human, for RLHF / judge calibration) and tournament shapes.

HTTP envelopes around `runner/pairwise_ops.py`. A verdict can be emitted by an
LLM judge or a human (via the web UI); both land here so the same pairs can be
compared for calibration.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class PairRefBody(BaseModel):
    """One side of a compared pair. Mirrors `schemas.pairwise_verdict.PairRef`."""

    kind: str
    trace_id: str | None = None
    case_id: str | None = None
    iteration_id: str | None = None
    content_snapshot: str | None = None


class IngestPairwiseVerdict(BaseModel):
    """A single verdict to ingest. `id`/`workspace_id` are assigned server-side."""

    a_ref: PairRefBody
    b_ref: PairRefBody
    preferred: str
    margin: float = 0.0
    rationale: str | None = None
    judge_kind: str
    judge_id: str
    judge_model: str | None = None
    rubric_version: int | None = None
    position: str | None = None
    case_id: str | None = None
    dataset_id: str | None = None


class IngestPairwiseRequest(BaseModel):
    verdicts: list[IngestPairwiseVerdict] = Field(default_factory=list)


class PairwiseVerdictResponse(BaseModel):
    id: str
    a_ref: PairRefBody
    b_ref: PairRefBody
    preferred: str
    margin: float
    rationale: str | None = None
    judge_kind: str
    judge_id: str
    rubric_version: int | None = None
    experiment_id: str | None = None
    case_id: str | None = None
    created_at: datetime


class PairwiseIngestSummaryResponse(BaseModel):
    ingested: int


class PairwiseCalibrationCellResponse(BaseModel):
    rubric_version: int | None = None
    compared_pairs: int
    agreements: int
    disagreements: int
    agreement_rate: float


class PairwiseCalibrationResponse(BaseModel):
    compared_pairs: int
    agreements: int
    disagreements: int
    agreement_rate: float
    by_rubric_version: list[PairwiseCalibrationCellResponse] = Field(default_factory=list)


# --- Pairwise tournaments (rank N candidates via Elo / Bradley-Terry) ---


class TournamentCandidateBody(BaseModel):
    """One entrant: a stable id, the text to judge, and an optional trace link."""

    id: str
    output_text: str
    trace_id: str | None = None


class RunTournamentRequest(BaseModel):
    candidates: list[TournamentCandidateBody] = Field(default_factory=list)
    judge_entrypoint: str
    """Dotted path `mod:fn` to the LLM judge callable (same shape as a grader's
    `judge_entrypoint`)."""
    rubric: str
    strategy: str = "all_pairs"
    method: str = "elo"
    baseline_id: str | None = None
    comparisons_per_candidate: int = 3
    swiss_rounds: int = 3
    swap_and_average: bool = False
    case_input: dict[str, Any] | None = None


class RankingRowResponse(BaseModel):
    candidate_id: str
    rank: int
    score: float
    wins: int
    losses: int
    ties: int
    n_comparisons: int


class TournamentResponse(BaseModel):
    id: str
    experiment_id: str | None = None
    strategy: str
    method: str
    candidate_ids: list[str]
    baseline_id: str | None = None
    n_comparisons: int
    swap_and_average: bool
    ranking: list[RankingRowResponse] = Field(default_factory=list)
    created_at: datetime
