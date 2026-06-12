"""Tests for the dataset-anchored baseline store (`selfevals.runner.baseline`).

Covers the deterministic id, load/set round-trips, and the idempotent auto-set:
the first completed run over a dataset becomes its baseline, and a second run
does NOT overwrite it.
"""

from __future__ import annotations

from pathlib import Path

from selfevals.optimization.aggregator import IterationAggregate
from selfevals.optimization.loop import IterationOutcome, OptimizationResult
from selfevals.runner.baseline import (
    baseline_id_for,
    load_baseline,
    maybe_autoset_baseline,
    set_baseline,
)
from selfevals.schemas.dataset import DatasetBaseline
from selfevals.schemas.enums import DecisionOutcome, IterationState, ProposerStrategy
from selfevals.schemas.iteration import (
    ExecutionInfo,
    IterationDecision,
    IterationMetrics,
    IterationRecord,
    MetricObservation,
    Proposal,
    ProposerInputs,
)
from selfevals.storage.factory import open_storage
from selfevals.storage.interface import WorkspaceScope

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"
DS = "ds_01HAAAAAAAAAAAAAAAAAAAAAAA"


def _scope(tmp_path: Path) -> WorkspaceScope:
    storage = open_storage(f"sqlite:///{tmp_path / 'db.sqlite'}")
    scope = storage.open(WS)
    return scope


def _iteration_record(itr_id: str = "itr_01HBBBBBBBBBBBBBBBBBBBBBBB") -> IterationRecord:
    return IterationRecord(
        id=itr_id,
        workspace_id=WS,
        experiment_id="exp_01HCCCCCCCCCCCCCCCCCCCCCCC",
        iteration=0,
        state=IterationState.COMPLETED,
        proposer=ProposerInputs(type=ProposerStrategy.GRID),
        hypothesis="baseline run",
        execution=ExecutionInfo(variant_id="v0"),
        metrics=IterationMetrics(
            primary=MetricObservation(name="pass@1", value=0.8),
            error_rate=0.0,
            confusion={"per_label_f1": {"refund": 0.9, "ship": 0.7}},
        ),
        decision=IterationDecision(outcome=DecisionOutcome.KEEP_CANDIDATE, rationale="ok"),
    )


def _aggregate(primary: float = 0.8) -> IterationAggregate:
    return IterationAggregate(primary_metric="pass@1", primary_value=primary)


def _result(spec_dataset_id: str, *, primary: float = 0.8) -> tuple[OptimizationResult, object]:
    """Build an OptimizationResult with one completed iteration, plus a stub spec.

    The spec only needs `.experiment.datasets.optimization.id` and
    `.experiment.id` — `maybe_autoset_baseline` reads nothing else.
    """
    from types import SimpleNamespace

    record = _iteration_record()
    outcome = IterationOutcome(
        iteration=0,
        proposal=Proposal(hypothesis="h"),
        aggregate=_aggregate(primary),
        case_runs=[],
        iteration_record=record,
        decision_record=SimpleNamespace(),  # unused by the baseline path
    )
    result = OptimizationResult(experiment=SimpleNamespace())  # type: ignore[arg-type]
    result.iterations.append(outcome)

    spec = SimpleNamespace(
        experiment=SimpleNamespace(
            id="exp_01HCCCCCCCCCCCCCCCCCCCCCCC",
            datasets=SimpleNamespace(optimization=SimpleNamespace(id=spec_dataset_id)),
        )
    )
    return result, spec


def test_baseline_id_is_deterministic_per_dataset() -> None:
    assert baseline_id_for(DS) == baseline_id_for(DS)
    assert baseline_id_for(DS).startswith("dbl_")
    assert baseline_id_for("ds_OTHER") != baseline_id_for(DS)


def test_load_returns_none_when_unset(tmp_path: Path) -> None:
    with _scope(tmp_path) as scope:
        assert load_baseline(scope, DS) is None


def test_set_and_load_round_trip(tmp_path: Path) -> None:
    with _scope(tmp_path) as scope:
        set_baseline(
            scope,
            dataset_id=DS,
            iteration_id="itr_X",
            experiment_id="exp_X",
            primary_metric="pass@1",
            primary_value=0.8,
            error_rate=0.05,
            confusion={"per_label_f1": {"a": 0.9}},
        )
        loaded = load_baseline(scope, DS)
        assert isinstance(loaded, DatasetBaseline)
        assert loaded.primary_value == 0.8
        assert loaded.error_rate == 0.05
        assert loaded.confusion == {"per_label_f1": {"a": 0.9}}


def test_set_overwrites_and_bumps_version(tmp_path: Path) -> None:
    with _scope(tmp_path) as scope:
        first = set_baseline(
            scope, dataset_id=DS, iteration_id="itr_1", experiment_id="exp_1",
            primary_metric="pass@1", primary_value=0.8,
        )
        assert first.version == 1
        second = set_baseline(
            scope, dataset_id=DS, iteration_id="itr_2", experiment_id="exp_2",
            primary_metric="pass@1", primary_value=0.9,
        )
        assert second.version == 2
        loaded = load_baseline(scope, DS)
        assert loaded is not None and loaded.iteration_id == "itr_2"


def test_autoset_creates_baseline_on_first_run(tmp_path: Path) -> None:
    with _scope(tmp_path) as scope:
        result, spec = _result(DS)
        created = maybe_autoset_baseline(scope, spec, result)  # type: ignore[arg-type]
        assert created is not None
        assert created.dataset_id == DS
        assert created.primary_value == 0.8
        # confusion carried from the best iteration's aggregate is None here
        # (the aggregate has no ConfusionReport), which is honest.
        loaded = load_baseline(scope, DS)
        assert loaded is not None and loaded.iteration_id == _iteration_record().id


def test_autoset_is_idempotent_second_run_does_not_overwrite(tmp_path: Path) -> None:
    with _scope(tmp_path) as scope:
        result1, spec1 = _result(DS, primary=0.8)
        maybe_autoset_baseline(scope, spec1, result1)  # type: ignore[arg-type]
        before = load_baseline(scope, DS)
        assert before is not None

        # A second, BETTER run over the same dataset must NOT move the baseline —
        # the baseline is the fixed starting point.
        result2, spec2 = _result(DS, primary=0.95)
        created = maybe_autoset_baseline(scope, spec2, result2)  # type: ignore[arg-type]
        assert created is None
        after = load_baseline(scope, DS)
        assert after is not None
        assert after.primary_value == 0.8  # unchanged.
        assert after.version == before.version


def test_autoset_skips_when_no_dataset_anchor(tmp_path: Path) -> None:
    from types import SimpleNamespace

    with _scope(tmp_path) as scope:
        result, _ = _result(DS)
        spec = SimpleNamespace(
            experiment=SimpleNamespace(
                id="exp_X",
                datasets=SimpleNamespace(optimization=SimpleNamespace(id="")),
            )
        )
        assert maybe_autoset_baseline(scope, spec, result) is None  # type: ignore[arg-type]


def test_autoset_swallows_storage_failure(tmp_path: Path) -> None:
    """A storage failure in the auto-set must never propagate into the run."""
    class _BoomScope:
        workspace_id = WS

        def get_entity(self, *a: object, **k: object) -> object:
            raise RuntimeError("storage exploded")

    result, spec = _result(DS)
    # Should return None (swallowed), not raise.
    assert maybe_autoset_baseline(_BoomScope(), spec, result) is None  # type: ignore[arg-type]
