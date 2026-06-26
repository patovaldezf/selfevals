from __future__ import annotations

from selfevals.optimization.ranking import (
    Outcome,
    bradley_terry,
    elo_ratings,
)


def _beats(winner: str, loser: str, n: int = 1) -> list[Outcome]:
    return [Outcome(winner_id=winner, loser_id=loser) for _ in range(n)]


# --- Elo ----------------------------------------------------------------


def test_elo_dominant_candidate_ranks_first() -> None:
    cands = ["a", "b", "c"]
    outcomes = _beats("a", "b", 3) + _beats("a", "c", 3) + _beats("b", "c", 3)
    ranking = elo_ratings(cands, outcomes)
    assert [r.candidate_id for r in ranking.rows] == ["a", "b", "c"]
    assert ranking.rows[0].wins == 6
    assert ranking.rows[-1].losses == 6


def test_elo_tie_splits_points() -> None:
    cands = ["a", "b"]
    ranking = elo_ratings(cands, [Outcome("a", "b", tie=True)])
    # A tie between equal-rated players leaves both at base.
    assert ranking.rows[0].score == ranking.rows[1].score
    assert ranking.rows[0].ties == 1


def test_elo_ignores_unknown_ids() -> None:
    ranking = elo_ratings(["a", "b"], [Outcome("a", "ghost")])
    assert {r.candidate_id for r in ranking.rows} == {"a", "b"}


# --- Bradley-Terry ------------------------------------------------------


def test_bradley_terry_transitive_order() -> None:
    cands = ["a", "b", "c"]
    outcomes = _beats("a", "b", 4) + _beats("b", "c", 4) + _beats("a", "c", 4)
    ranking = bradley_terry(cands, outcomes)
    ids = [r.candidate_id for r in ranking.rows]
    assert ids == ["a", "b", "c"]
    # Strengths are normalized and sum to ~1.
    assert abs(sum(r.score for r in ranking.rows) - 1.0) < 1e-6


def test_bradley_terry_dominant_gets_highest_strength() -> None:
    cands = ["x", "y", "z"]
    outcomes = _beats("x", "y", 5) + _beats("x", "z", 5)
    ranking = bradley_terry(cands, outcomes)
    assert ranking.rows[0].candidate_id == "x"
    assert ranking.rows[0].score > ranking.rows[1].score


def test_bradley_terry_single_candidate() -> None:
    ranking = bradley_terry(["solo"], [])
    assert len(ranking.rows) == 1
    assert ranking.rows[0].candidate_id == "solo"


def test_bradley_terry_empty() -> None:
    ranking = bradley_terry([], [])
    assert ranking.rows == []


def test_bradley_terry_tie_does_not_crash_and_counts() -> None:
    cands = ["a", "b"]
    ranking = bradley_terry(cands, [Outcome("a", "b", tie=True)])
    rows = {r.candidate_id: r for r in ranking.rows}
    assert rows["a"].ties == 1
    assert rows["b"].ties == 1


def test_ranking_deterministic_tiebreak_by_id() -> None:
    # Two candidates with identical records → stable order by id.
    cands = ["b", "a"]
    ranking = elo_ratings(cands, [])
    assert [r.candidate_id for r in ranking.rows] == ["a", "b"]
