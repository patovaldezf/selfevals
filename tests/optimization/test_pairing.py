from __future__ import annotations

from selfevals.optimization.pairing import (
    all_pairs,
    next_swiss_round,
    sampled,
    vs_baseline,
)


def _norm(pairs: list[tuple[str, str]]) -> set[tuple[str, str]]:
    return {(a, b) if a < b else (b, a) for a, b in pairs}


def test_vs_baseline_is_linear() -> None:
    cands = ["a", "b", "c", "base"]
    pairs = vs_baseline(cands, "base")
    assert len(pairs) == 3  # n-1
    assert all("base" in p for p in pairs)


def test_all_pairs_is_quadratic() -> None:
    cands = ["a", "b", "c", "d"]
    pairs = all_pairs(cands)
    assert len(pairs) == 6  # n(n-1)/2
    assert len(_norm(pairs)) == 6  # no duplicates


def test_all_pairs_dedups_input() -> None:
    pairs = all_pairs(["a", "a", "b"])
    assert len(pairs) == 1


def test_sampled_respects_budget_and_dedups() -> None:
    cands = [f"c{i}" for i in range(10)]
    pairs = sampled(cands, comparisons_per_candidate=3)
    # No pair appears twice.
    assert len(_norm(pairs)) == len(pairs)
    # Far fewer than all_pairs (45).
    assert len(pairs) < len(all_pairs(cands))
    # Every candidate appears at least once.
    seen = {c for pair in pairs for c in pair}
    assert seen == set(cands)


def test_sampled_is_deterministic() -> None:
    cands = [f"c{i}" for i in range(8)]
    assert sampled(cands, comparisons_per_candidate=2) == sampled(
        cands, comparisons_per_candidate=2
    )


def test_sampled_budget_capped_at_round_robin() -> None:
    cands = ["a", "b", "c"]
    # Budget larger than n-1 → at most the full round-robin.
    pairs = sampled(cands, comparisons_per_candidate=10)
    assert len(_norm(pairs)) <= 3


def test_sampled_trivial_inputs() -> None:
    assert sampled(["a"], comparisons_per_candidate=3) == []
    assert sampled(["a", "b"], comparisons_per_candidate=0) == []


def test_swiss_round_pairs_nearest_and_skips_played() -> None:
    cands = ["a", "b", "c", "d"]
    standings = {"a": 3.0, "b": 2.0, "c": 1.0, "d": 0.0}
    # a-b already played → a should pair with c instead.
    pairs = next_swiss_round(cands, standings, already_played={("a", "b")})
    norm = _norm(pairs)
    assert ("a", "c") in norm
    assert ("a", "b") not in norm
    # Roughly n/2 pairs.
    assert len(pairs) == 2


def test_swiss_round_all_played_leaves_unpaired() -> None:
    cands = ["a", "b"]
    played = {("a", "b")}
    assert next_swiss_round(cands, {"a": 1.0, "b": 0.0}, played) == []
