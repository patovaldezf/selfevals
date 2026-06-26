"""Ranking aggregation for pairwise tournaments — Elo and Bradley-Terry.

Pure functions over a list of `Outcome`s (no storage, no I/O), the same shape as
`ci/regression.py` and `graders/_confusion.py` so the math is unit-testable in
isolation. A pairwise tournament produces many head-to-head `Outcome`s; these
turn that into a total order over the candidates.

Two methods, both dependency-free (CLAUDE.md keeps core deps minimal — no numpy
/ scipy):

* `elo_ratings` — incremental Elo. Cheap, order-sensitive, good for streaming or
  a quick rank.
* `bradley_terry` — maximum-likelihood strengths via Hunter's MM algorithm
  (2004), a ~20-line fixed-point iteration that needs no gradient solver. More
  principled for a fixed batch of comparisons.

Determinism: results never depend on wall-clock or RNG. Elo applies outcomes in
the caller's order (the orchestrator sorts by verdict id), and Bradley-Terry is
order-independent by construction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

RankingMethod = Literal["elo", "bradley_terry"]


@dataclass(frozen=True)
class Outcome:
    """One head-to-head result, projected from a `PairwiseVerdict`.

    For a non-tie, `winner_id` beat `loser_id`. For a tie, the two ids are still
    given (order irrelevant) and `tie=True`. `weight` lets a decisive margin
    count more than a coin-flip; default 1.0 keeps every comparison equal.
    """

    winner_id: str
    loser_id: str
    tie: bool = False
    weight: float = 1.0


@dataclass(frozen=True)
class RankingRow:
    candidate_id: str
    score: float
    wins: int
    losses: int
    ties: int
    n_comparisons: int


@dataclass(frozen=True)
class Ranking:
    method: RankingMethod
    rows: list[RankingRow] = field(default_factory=list)
    """Sorted best-first (highest score)."""


def _tally(
    candidates: list[str], outcomes: list[Outcome]
) -> dict[str, tuple[int, int, int]]:
    """Per-candidate (wins, losses, ties) counts."""
    wins = dict.fromkeys(candidates, 0)
    losses = dict.fromkeys(candidates, 0)
    ties = dict.fromkeys(candidates, 0)
    for o in outcomes:
        if o.tie:
            if o.winner_id in ties:
                ties[o.winner_id] += 1
            if o.loser_id in ties:
                ties[o.loser_id] += 1
        else:
            if o.winner_id in wins:
                wins[o.winner_id] += 1
            if o.loser_id in losses:
                losses[o.loser_id] += 1
    return {c: (wins[c], losses[c], ties[c]) for c in candidates}


def _rows(
    candidates: list[str], scores: dict[str, float], outcomes: list[Outcome]
) -> list[RankingRow]:
    counts = _tally(candidates, outcomes)
    rows = [
        RankingRow(
            candidate_id=c,
            score=scores[c],
            wins=counts[c][0],
            losses=counts[c][1],
            ties=counts[c][2],
            n_comparisons=sum(counts[c]),
        )
        for c in candidates
    ]
    # Best-first; ties in score broken by candidate id for determinism.
    rows.sort(key=lambda r: (-r.score, r.candidate_id))
    return rows


def elo_ratings(
    candidates: list[str],
    outcomes: list[Outcome],
    *,
    k: float = 32.0,
    base: float = 1500.0,
) -> Ranking:
    """Incremental Elo. Outcomes are applied in the given order.

    A tie scores 0.5 for each side. The expected score uses the standard
    logistic with a 400-point scale. `k` is the update step; `base` the starting
    rating. Unknown ids in an outcome are ignored (defensive).
    """
    ratings = dict.fromkeys(candidates, base)
    known = set(candidates)
    for o in outcomes:
        if o.winner_id not in known or o.loser_id not in known:
            continue
        ra = ratings[o.winner_id]
        rb = ratings[o.loser_id]
        ea = 1.0 / (1.0 + 10.0 ** ((rb - ra) / 400.0))
        eb = 1.0 - ea
        sa, sb = (0.5, 0.5) if o.tie else (1.0, 0.0)
        ratings[o.winner_id] = ra + k * o.weight * (sa - ea)
        ratings[o.loser_id] = rb + k * o.weight * (sb - eb)
    return Ranking(method="elo", rows=_rows(candidates, ratings, outcomes))


def bradley_terry(
    candidates: list[str],
    outcomes: list[Outcome],
    *,
    max_iter: int = 100,
    tol: float = 1e-6,
) -> Ranking:
    """Bradley-Terry strengths via Hunter's MM algorithm (2004).

    Maximizes the likelihood that `winner` beats `loser` under
    `P(i beats j) = p_i / (p_i + p_j)`, iterating
    `p_i <- wins_i / sum_j (n_ij / (p_i + p_j))` to convergence and renormalizing
    each round. No gradient solver, no external deps.

    Ties are split as half a win to each side (the common MVP treatment; a full
    Rao-Kupper tie model is future work). The returned `score` is the normalized
    strength; rows are sorted by it.
    """
    n = len(candidates)
    if n == 0:
        return Ranking(method="bradley_terry", rows=[])
    if n == 1:
        return Ranking(
            method="bradley_terry",
            rows=_rows(candidates, {candidates[0]: 1.0}, outcomes),
        )

    idx = {c: i for i, c in enumerate(candidates)}
    # wins[i]: fractional wins (tie = 0.5). games[i][j]: times i and j met.
    wins = [0.0] * n
    games = [[0.0] * n for _ in range(n)]
    for o in outcomes:
        if o.winner_id not in idx or o.loser_id not in idx:
            continue
        i, j = idx[o.winner_id], idx[o.loser_id]
        w = o.weight
        games[i][j] += w
        games[j][i] += w
        if o.tie:
            wins[i] += 0.5 * w
            wins[j] += 0.5 * w
        else:
            wins[i] += w

    p = [1.0] * n
    for _ in range(max_iter):
        new_p = [0.0] * n
        for i in range(n):
            denom = 0.0
            for j in range(n):
                if i == j:
                    continue
                nij = games[i][j]
                if nij:
                    denom += nij / (p[i] + p[j])
            new_p[i] = wins[i] / denom if denom > 0 else p[i]
        total = sum(new_p) or 1.0
        new_p = [x / total for x in new_p]
        delta = max(abs(new_p[i] - p[i]) for i in range(n))
        p = new_p
        if delta < tol:
            break

    scores = {c: p[idx[c]] for c in candidates}
    return Ranking(method="bradley_terry", rows=_rows(candidates, scores, outcomes))
