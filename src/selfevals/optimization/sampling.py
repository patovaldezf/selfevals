"""Select which EvalCases enter the optimization loop.

This module consumes the sampling contract the schema layer has long
defined but no runtime ever read:

- `RunSpec.sample_strategy` (`full` / `random_subset` / `stratified`),
- `RunSpec.seed` for reproducibility (spec §2.3),
- `SplitAllocation` fractions (`optimization` / `holdout`; spec §5 portfolio),
- `EvalCase.holdout` (spec §11 anti-overfitting: hold out gate datasets from
  the optimization loop).

The functions are pure and deterministic: the same `(cases, run_spec,
split_allocation)` always yields the same split. Randomness is seeded from
`RunSpec.seed` (falling back to a fixed default when unset), so two calls with
the same inputs return identical sets.

The cardinal invariant: `holdout=True` cases NEVER enter the optimization set.
They are returned separately so callers can run them as a held-out gate
without contaminating the optimizer.
"""

from __future__ import annotations

import logging
import random
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from selfevals.schemas.dataset import SplitAllocation
    from selfevals.schemas.eval_case import EvalCase
    from selfevals.schemas.experiment import RunSpec

logger = logging.getLogger("selfevals.sampling")

# Used when RunSpec.seed is None so a `random_subset` / `stratified` run is
# still reproducible across processes (spec §2.3).
_DEFAULT_SEED = 0


@dataclass(frozen=True)
class OptimizationSplit:
    """The outcome of partitioning cases for an optimization run.

    `optimization` is the set the loop evaluates and proposers may search
    against. `holdout` is the reserved set that must stay out of the
    optimization loop (spec §11). Both lists preserve the input order of the
    cases they contain.
    """

    optimization: list[EvalCase]
    holdout: list[EvalCase]


def select_optimization_set(
    cases: list[EvalCase],
    run_spec: RunSpec,
    *,
    split_allocation: SplitAllocation | None = None,
) -> OptimizationSplit:
    """Partition `cases` into an optimization set and a holdout set.

    `holdout=True` cases are always excluded from the optimization set and
    returned under `OptimizationSplit.holdout`. The non-holdout pool is then
    reduced according to `run_spec.sample_strategy`:

    - ``full`` (default): the whole non-holdout pool.
    - ``random_subset``: a deterministic random subset whose size is
      ``round(fraction * len(pool))``, where ``fraction`` is
      ``split_allocation.optimization`` when a `SplitAllocation` is supplied,
      else ``1.0`` (the whole pool).
    - ``stratified``: a deterministic subset that holds the same per-feature
      proportions as the pool, stratified by ``taxonomy.feature.primary``.
      The target size uses the same ``fraction`` rule as ``random_subset``.

    The optimization set preserves the original input order of the cases it
    contains. Sampling is seeded from ``run_spec.seed`` (or a fixed default
    when unset), so repeated calls with identical inputs are identical.
    """
    holdout = [c for c in cases if c.holdout]
    pool = [c for c in cases if not c.holdout]

    strategy = run_spec.sample_strategy
    if strategy == "full":
        selected = pool
    elif strategy == "random_subset":
        selected = _random_subset(pool, _fraction(split_allocation), seed=_seed(run_spec))
    elif strategy == "stratified":
        selected = _stratified_subset(pool, _fraction(split_allocation), seed=_seed(run_spec))
    else:  # pragma: no cover - Literal type makes this unreachable
        raise ValueError(f"unknown sample_strategy {strategy!r}")

    # Surface (don't hide) a subsampled case pool so a caller can see the run
    # evaluated fewer cases than were supplied.
    if strategy != "full" and len(selected) < len(pool):
        logger.info(
            "sample_strategy=%s subsampled %d->%d cases", strategy, len(pool), len(selected)
        )

    return OptimizationSplit(optimization=selected, holdout=holdout)


def _seed(run_spec: RunSpec) -> int:
    return run_spec.seed if run_spec.seed is not None else _DEFAULT_SEED


def _fraction(split_allocation: SplitAllocation | None) -> float:
    """The fraction of the non-holdout pool to keep.

    Holdout membership is already enforced per-case via `EvalCase.holdout`, so
    `SplitAllocation.optimization` here is read only as the subset size knob
    for the `random_subset` / `stratified` strategies. Without an allocation we
    keep the whole pool.
    """
    if split_allocation is None:
        return 1.0
    return split_allocation.optimization


def _target_size(pool_size: int, fraction: float) -> int:
    """How many cases to keep, clamped to ``[0, pool_size]``.

    A positive fraction over a non-empty pool keeps at least one case so a
    run is never silently empty.
    """
    if pool_size == 0:
        return 0
    target = round(fraction * pool_size)
    if fraction > 0.0 and target == 0:
        target = 1
    return max(0, min(pool_size, target))


def _random_subset(pool: list[EvalCase], fraction: float, *, seed: int) -> list[EvalCase]:
    target = _target_size(len(pool), fraction)
    if target >= len(pool):
        return list(pool)
    rng = random.Random(seed)
    chosen = set(rng.sample(range(len(pool)), target))
    # Preserve input order rather than sample order so the result is stable and
    # readable regardless of which indices the RNG drew.
    return [c for i, c in enumerate(pool) if i in chosen]


def _stratified_subset(pool: list[EvalCase], fraction: float, *, seed: int) -> list[EvalCase]:
    """Keep ``fraction`` of each stratum, stratified by feature.

    The coverage dimension is ``taxonomy.feature.primary`` — the same axis the
    portfolio reports on (spec §5) — so a reduced optimization set still spans
    every feature present in the pool proportionally. Within each stratum the
    subset is drawn with a per-stratum seeded RNG; the final list preserves the
    pool's input order.
    """
    target = _target_size(len(pool), fraction)
    if target >= len(pool):
        return list(pool)
    if target == 0:
        return []

    # Group indices by feature, preserving first-seen order for determinism.
    strata: dict[str, list[int]] = OrderedDict()
    for i, case in enumerate(pool):
        strata.setdefault(case.taxonomy.feature.primary, []).append(i)

    # Largest-remainder apportionment: floor(share) per stratum, then hand the
    # leftover seats to the strata with the largest fractional remainders. This
    # keeps per-feature proportions as close to `fraction` as integer counts
    # allow, and is fully deterministic.
    raw = {key: fraction * len(idxs) for key, idxs in strata.items()}
    counts = {key: int(value) for key, value in raw.items()}
    assigned = sum(counts.values())
    leftover = target - assigned
    if leftover > 0:
        remainders = sorted(
            strata.keys(),
            key=lambda key: (-(raw[key] - counts[key]), key),
        )
        for key in remainders[:leftover]:
            counts[key] = min(counts[key] + 1, len(strata[key]))

    chosen: set[int] = set()
    for key, idxs in strata.items():
        take = min(counts[key], len(idxs))
        if take <= 0:
            continue
        rng = random.Random(f"{seed}:{key}")
        chosen.update(rng.sample(idxs, take))

    selected = [c for i, c in enumerate(pool) if i in chosen]
    # Top up deterministically if rounding left us short of the target (e.g.
    # many singleton strata floored to zero), drawing from the unchosen pool.
    if len(selected) < target:
        remaining = [i for i in range(len(pool)) if i not in chosen]
        rng = random.Random(f"{seed}:__topup__")
        extra = rng.sample(remaining, min(target - len(selected), len(remaining)))
        chosen.update(extra)
        selected = [c for i, c in enumerate(pool) if i in chosen]
    return selected
