from __future__ import annotations

import logging
from collections import Counter

import pytest

from selfevals.optimization.sampling import (
    OptimizationSplit,
    select_optimization_set,
)
from selfevals.schemas.dataset import SplitAllocation
from selfevals.schemas.enums import (
    DatasetSource,
    DatasetType,
    GroundTruthMethod,
    Level,
    SandboxMode,
)
from selfevals.schemas.eval_case import (
    CaseTaxonomy,
    EvalCase,
    Expected,
    FeatureTag,
    GroundTruthSpec,
    SourceInfo,
)
from selfevals.schemas.experiment import RunSpec

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _case(*, feature: str = "commerce.product_resolution", holdout: bool = False) -> EvalCase:
    return EvalCase(
        id=EvalCase.make_id(),
        workspace_id=WS,
        name="t",
        task_type="x",
        input={"messages": [{"role": "user", "content": "hi"}]},
        taxonomy=CaseTaxonomy(
            level=Level.FINAL_RESPONSE,
            feature=FeatureTag(primary=feature),
            source=SourceInfo(type=DatasetSource.HANDCRAFTED),
            ground_truth=GroundTruthSpec(methods=[GroundTruthMethod.EXACT_MATCH]),
            dataset_type=DatasetType.CAPABILITY,
        ),
        expected=Expected(must_include=["pong"]),
        holdout=holdout,
    )


def _run(*, strategy: str = "full", seed: int | None = None) -> RunSpec:
    return RunSpec(sandbox=SandboxMode.MOCK, sample_strategy=strategy, seed=seed)


# --- holdout exclusion -----------------------------------------------------


def test_holdout_excluded_from_optimization_set() -> None:
    opt = [_case() for _ in range(3)]
    held = [_case(holdout=True) for _ in range(2)]
    split = select_optimization_set([*opt, *held], _run(strategy="full"))
    assert isinstance(split, OptimizationSplit)
    opt_ids = {c.id for c in split.optimization}
    held_ids = {c.id for c in split.holdout}
    assert opt_ids == {c.id for c in opt}
    assert held_ids == {c.id for c in held}
    # No holdout case ever leaks into the optimization set.
    assert all(not c.holdout for c in split.optimization)
    assert opt_ids.isdisjoint(held_ids)


def test_holdout_excluded_under_every_strategy() -> None:
    cases = [_case() for _ in range(6)] + [_case(holdout=True) for _ in range(3)]
    for strategy in ("full", "random_subset", "stratified"):
        split = select_optimization_set(
            cases,
            _run(strategy=strategy, seed=7),
            split_allocation=SplitAllocation(optimization=0.5, holdout=0.4, reliability=0.1),
        )
        assert all(not c.holdout for c in split.optimization)
        assert len(split.holdout) == 3


# --- full ------------------------------------------------------------------


def test_full_returns_all_non_holdout() -> None:
    cases = [_case() for _ in range(5)]
    split = select_optimization_set(cases, _run(strategy="full"))
    assert [c.id for c in split.optimization] == [c.id for c in cases]
    assert split.holdout == []


def test_full_ignores_split_allocation_fraction() -> None:
    cases = [_case() for _ in range(5)]
    split = select_optimization_set(
        cases,
        _run(strategy="full"),
        split_allocation=SplitAllocation(optimization=0.2, holdout=0.7, reliability=0.1),
    )
    # `full` means the whole non-holdout pool regardless of the fraction.
    assert len(split.optimization) == 5


# --- random_subset ---------------------------------------------------------


def test_random_subset_is_deterministic_with_seed() -> None:
    cases = [_case() for _ in range(10)]
    alloc = SplitAllocation(optimization=0.5, holdout=0.4, reliability=0.1)
    a = select_optimization_set(
        cases, _run(strategy="random_subset", seed=42), split_allocation=alloc
    )
    b = select_optimization_set(
        cases, _run(strategy="random_subset", seed=42), split_allocation=alloc
    )
    assert [c.id for c in a.optimization] == [c.id for c in b.optimization]
    assert len(a.optimization) == 5


def test_random_subset_different_seed_can_differ() -> None:
    cases = [_case() for _ in range(20)]
    alloc = SplitAllocation(optimization=0.5, holdout=0.4, reliability=0.1)
    a = select_optimization_set(
        cases, _run(strategy="random_subset", seed=1), split_allocation=alloc
    )
    b = select_optimization_set(
        cases, _run(strategy="random_subset", seed=2), split_allocation=alloc
    )
    assert len(a.optimization) == len(b.optimization) == 10
    # Two different seeds over 20 cases choosing 10 are extremely unlikely to match.
    assert [c.id for c in a.optimization] != [c.id for c in b.optimization]


def test_random_subset_preserves_input_order() -> None:
    cases = [_case() for _ in range(10)]
    alloc = SplitAllocation(optimization=0.5, holdout=0.4, reliability=0.1)
    split = select_optimization_set(
        cases, _run(strategy="random_subset", seed=3), split_allocation=alloc
    )
    chosen_ids = [c.id for c in split.optimization]
    original_positions = [cases.index(c) for c in split.optimization]
    assert original_positions == sorted(original_positions)
    assert len(set(chosen_ids)) == len(chosen_ids)


def test_random_subset_without_allocation_keeps_whole_pool() -> None:
    cases = [_case() for _ in range(4)]
    split = select_optimization_set(cases, _run(strategy="random_subset", seed=9))
    assert len(split.optimization) == 4


def test_random_subset_keeps_at_least_one() -> None:
    cases = [_case() for _ in range(3)]
    alloc = SplitAllocation(optimization=0.01, holdout=0.98, reliability=0.01)
    split = select_optimization_set(
        cases, _run(strategy="random_subset", seed=5), split_allocation=alloc
    )
    assert len(split.optimization) == 1


# --- stratified ------------------------------------------------------------


def test_stratified_spreads_across_features() -> None:
    cases = (
        [_case(feature="a") for _ in range(6)]
        + [_case(feature="b") for _ in range(6)]
        + [_case(feature="c") for _ in range(6)]
    )
    alloc = SplitAllocation(optimization=0.5, holdout=0.4, reliability=0.1)
    split = select_optimization_set(
        cases, _run(strategy="stratified", seed=11), split_allocation=alloc
    )
    by_feature = Counter(c.taxonomy.feature.primary for c in split.optimization)
    assert len(split.optimization) == 9
    # Each feature contributes proportionally: 0.5 * 6 = 3 per stratum.
    assert by_feature == Counter({"a": 3, "b": 3, "c": 3})


def test_stratified_is_deterministic_with_seed() -> None:
    cases = [_case(feature="a") for _ in range(8)] + [_case(feature="b") for _ in range(4)]
    alloc = SplitAllocation(optimization=0.5, holdout=0.4, reliability=0.1)
    a = select_optimization_set(cases, _run(strategy="stratified", seed=21), split_allocation=alloc)
    b = select_optimization_set(cases, _run(strategy="stratified", seed=21), split_allocation=alloc)
    assert [c.id for c in a.optimization] == [c.id for c in b.optimization]


def test_stratified_largest_remainder_hits_target() -> None:
    # 5 features x 3 cases = 15; fraction 0.5 -> target 8. Each stratum floors
    # to 1 (0.5*3=1.5 -> 1), assigning 5; the 3 leftover seats go to the
    # largest remainders, hitting exactly 8.
    cases = [_case(feature=f"f{f}") for f in range(5) for _ in range(3)]
    alloc = SplitAllocation(optimization=0.5, holdout=0.4, reliability=0.1)
    split = select_optimization_set(
        cases, _run(strategy="stratified", seed=4), split_allocation=alloc
    )
    assert len(split.optimization) == 8
    by_feature = Counter(c.taxonomy.feature.primary for c in split.optimization)
    # Every feature keeps at least one case so coverage is preserved.
    assert all(v >= 1 for v in by_feature.values())


def test_stratified_full_fraction_keeps_pool() -> None:
    cases = [_case(feature="a") for _ in range(3)] + [_case(feature="b") for _ in range(3)]
    split = select_optimization_set(cases, _run(strategy="stratified", seed=1))
    assert len(split.optimization) == 6


# --- reproducibility with default seed -------------------------------------


def test_unset_seed_is_still_reproducible() -> None:
    cases = [_case() for _ in range(10)]
    alloc = SplitAllocation(optimization=0.5, holdout=0.4, reliability=0.1)
    a = select_optimization_set(
        cases, _run(strategy="random_subset", seed=None), split_allocation=alloc
    )
    b = select_optimization_set(
        cases, _run(strategy="random_subset", seed=None), split_allocation=alloc
    )
    assert [c.id for c in a.optimization] == [c.id for c in b.optimization]


def test_empty_input_yields_empty_split() -> None:
    split = select_optimization_set([], _run(strategy="full"))
    assert split.optimization == []
    assert split.holdout == []


# --- subsampling is surfaced, not silent ----------------------------------


def test_random_subset_logs_subsampling(caplog: pytest.LogCaptureFixture) -> None:
    cases = [_case() for _ in range(10)]
    alloc = SplitAllocation(optimization=0.5, holdout=0.4, reliability=0.1)
    with caplog.at_level(logging.INFO, logger="selfevals.sampling"):
        split = select_optimization_set(
            cases, _run(strategy="random_subset", seed=42), split_allocation=alloc
        )
    assert len(split.optimization) == 5
    msgs = [
        r.getMessage()
        for r in caplog.records
        if r.name == "selfevals.sampling" and r.levelno == logging.INFO
    ]
    assert any("subsampled 10->5 cases" in m and "random_subset" in m for m in msgs)


def test_full_does_not_log_subsampling(caplog: pytest.LogCaptureFixture) -> None:
    cases = [_case() for _ in range(5)]
    with caplog.at_level(logging.INFO, logger="selfevals.sampling"):
        select_optimization_set(cases, _run(strategy="full"))
    assert not any("subsampled" in r.getMessage() for r in caplog.records)
