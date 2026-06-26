"""Pairing strategies for a pairwise tournament — how to choose which pairs to
compare, trading coverage against cost.

Comparing everything against everything is O(n²), which gets expensive fast (the
judge is an LLM call per pair). These pure functions return the list of
`(a_id, b_id)` pairs a tournament should judge, at three cost points:

* `vs_baseline` — O(n): each candidate vs one fixed baseline. "Did my change
  beat the incumbent?"
* `all_pairs` — O(n²): full round-robin. The most robust ranking; for few, high-
  value candidates.
* `sampled` — sub-O(n²): each candidate gets a fixed budget of random opponents.
  Approximate ranking without paying for every pair.

`swiss` is the adaptive sub-O(n²) option, but it cannot be planned up front — it
needs the running standings between rounds — so it lives in the orchestrator
(`runner/pairwise_tournament.py`) as `next_swiss_round`, which this module
provides as a pure helper too.

Determinism: any "randomness" is derived from a stable hash of the candidate ids
(never `random`/`Date.now`), so a tournament is reproducible.
"""

from __future__ import annotations

import hashlib

Pair = tuple[str, str]


def _stable_shuffle(items: list[str], *, salt: str) -> list[str]:
    """Deterministic shuffle: order by a hash of (salt, id).

    Reproducible across runs and machines — no global RNG, no wall-clock — which
    the runtime requires and which makes a tournament's pairing auditable.
    """

    def key(item: str) -> str:
        return hashlib.sha256(f"{salt}:{item}".encode()).hexdigest()

    return sorted(items, key=key)


def vs_baseline(candidates: list[str], baseline_id: str) -> list[Pair]:
    """O(n): every candidate (except the baseline) paired against the baseline."""
    return [(c, baseline_id) for c in candidates if c != baseline_id]


def all_pairs(candidates: list[str]) -> list[Pair]:
    """O(n²): full round-robin, each unordered pair once. Order is stable."""
    ordered = sorted(set(candidates))
    pairs: list[Pair] = []
    for i in range(len(ordered)):
        for j in range(i + 1, len(ordered)):
            pairs.append((ordered[i], ordered[j]))
    return pairs


def sampled(
    candidates: list[str],
    *,
    comparisons_per_candidate: int,
    salt: str = "selfevals-tournament",
) -> list[Pair]:
    """Sub-O(n²): each candidate meets up to `comparisons_per_candidate` distinct
    opponents, deduped so a pair is judged once.

    Opponents are picked deterministically (stable-shuffled rotation), so the
    total is ~`n * k / 2` pairs rather than `n(n-1)/2`. Capped at the round-robin
    size when the budget exceeds it.
    """
    ordered = sorted(set(candidates))
    n = len(ordered)
    if n < 2 or comparisons_per_candidate < 1:
        return []
    k = min(comparisons_per_candidate, n - 1)
    seen: set[Pair] = set()
    pairs: list[Pair] = []
    for c in ordered:
        # A per-candidate stable order of the other ids; take the first k.
        others = _stable_shuffle([o for o in ordered if o != c], salt=f"{salt}:{c}")
        for opp in others[:k]:
            key = (c, opp) if c < opp else (opp, c)
            if key not in seen:
                seen.add(key)
                pairs.append(key)
    return pairs


def next_swiss_round(
    candidates: list[str],
    standings: dict[str, float],
    already_played: set[Pair],
) -> list[Pair]:
    """One Swiss round: pair candidates with the nearest score not yet played.

    `standings` maps candidate -> current score (e.g. running wins or Elo). Sort
    by score, then greedily pair each unpaired candidate with the next available
    one it hasn't met. Returns the pairs for this round (≈ n/2). The orchestrator
    runs this `rounds` times, updating `standings` between rounds — total cost
    ≈ rounds * n / 2, well under O(n²) for small `rounds`.
    """
    ordered = sorted(candidates, key=lambda c: (-standings.get(c, 0.0), c))
    paired: set[str] = set()
    pairs: list[Pair] = []
    for i, a in enumerate(ordered):
        if a in paired:
            continue
        for b in ordered[i + 1 :]:
            if b in paired:
                continue
            key = (a, b) if a < b else (b, a)
            if key in already_played:
                continue
            pairs.append((a, b))
            paired.add(a)
            paired.add(b)
            break
    return pairs
