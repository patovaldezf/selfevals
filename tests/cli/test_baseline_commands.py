"""CLI tests for `selfevals baseline` and `selfevals regression`.

End-to-end through `app()`: seed a dataset + a completed iteration into storage,
then drive show/set/check and assert the gate's exit codes (0 ok, 1 regression,
2 usage error).
"""

from __future__ import annotations

import pytest

from selfevals.cli.main import app
from selfevals.runner.launch import ensure_workspace_by_id
from selfevals.schemas.dataset import Dataset
from selfevals.schemas.enums import (
    DatasetType,
    DecisionOutcome,
    IterationState,
    ProposerStrategy,
)
from selfevals.schemas.iteration import (
    ExecutionInfo,
    IterationDecision,
    IterationMetrics,
    IterationRecord,
    MetricObservation,
    ProposerInputs,
)
from selfevals.storage.factory import open_storage
from tests.cli.test_cli import _experiment

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"
DS = "ds_01HAAAAAAAAAAAAAAAAAAAAAAA"
EXP = "exp_01HCCCCCCCCCCCCCCCCCCCCCCC"


def _capture(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, str, str]:
    rc = app(argv)
    out = capsys.readouterr()
    return rc, out.out, out.err


def _iteration(
    itr_id: str,
    *,
    primary: float,
    error_rate: float = 0.0,
    f1: dict[str, float | None] | None = None,
    iteration: int = 0,
) -> IterationRecord:
    confusion = {"per_label_f1": f1} if f1 is not None else None
    return IterationRecord(
        id=itr_id,
        workspace_id=WS,
        experiment_id=EXP,
        iteration=iteration,
        state=IterationState.COMPLETED,
        proposer=ProposerInputs(type=ProposerStrategy.GRID),
        hypothesis="run",
        execution=ExecutionInfo(variant_id="v0"),
        metrics=IterationMetrics(
            primary=MetricObservation(name="pass@1", value=primary),
            error_rate=error_rate,
            confusion=confusion,
        ),
        decision=IterationDecision(outcome=DecisionOutcome.KEEP_CANDIDATE, rationale="ok"),
    )


def _seed(db_url: str, *iterations: IterationRecord) -> None:
    storage = open_storage(db_url)
    try:
        ensure_workspace_by_id(storage, WS)
        with storage.open(WS) as scope:
            # Postgres enforces the iteration→experiment FK, so the experiment
            # row must exist before its iterations.
            scope.put_entity(_experiment(WS, id=EXP))
            scope.put_entity(
                Dataset(id=DS, workspace_id=WS, name="golden", dataset_type=DatasetType.CAPABILITY)
            )
            for itr in iterations:
                scope.put_entity(itr)
    finally:
        storage.close()


def test_show_reports_no_baseline_then_set_then_show(
    db_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    base = _iteration("itr_01HBBBBBBBBBBBBBBBBBBBBBB1", primary=0.8, f1={"a": 0.9})
    _seed(db_url, base)

    rc, out, _ = _capture(capsys, ["--db", db_url, "baseline", "show", WS, "--dataset", DS])
    assert rc == 0
    assert "no baseline set" in out

    rc, out, _ = _capture(
        capsys,
        ["--db", db_url, "baseline", "set", WS, "--dataset", DS, "--iteration", base.id],
    )
    assert rc == 0, out
    assert "baseline set for dataset" in out

    rc, out, _ = _capture(capsys, ["--db", db_url, "baseline", "show", WS, "--dataset", DS])
    assert rc == 0
    assert base.id in out
    assert "pass@1: 0.8" in out


def test_regression_check_passes_when_no_regression(
    db_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    base = _iteration("itr_BASE", primary=0.8, f1={"a": 0.9})
    cur = _iteration("itr_CUR", primary=0.85, f1={"a": 0.92}, iteration=1)  # better.
    _seed(db_url, base, cur)
    _capture(capsys, ["--db", db_url, "baseline", "set", WS, "--dataset", DS, "--iteration", base.id])

    rc, out, _ = _capture(
        capsys,
        ["--db", db_url, "regression", "check", WS, "--dataset", DS, "--iteration", cur.id],
    )
    assert rc == 0, out
    assert "no regression" in out


def test_regression_check_fails_on_primary_drop(
    db_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    base = _iteration("itr_BASE", primary=0.8)
    cur = _iteration("itr_CUR", primary=0.6, iteration=1)  # dropped 20 points.
    _seed(db_url, base, cur)
    _capture(capsys, ["--db", db_url, "baseline", "set", WS, "--dataset", DS, "--iteration", base.id])

    rc, out, _ = _capture(
        capsys,
        ["--db", db_url, "regression", "check", WS, "--dataset", DS, "--iteration", cur.id],
    )
    assert rc == 1, out
    assert "REGRESSION" in out


def test_regression_check_fails_on_per_class_f1_drop(
    db_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    # Aggregate pass@1 holds, but one class's F1 collapses.
    base = _iteration("itr_BASE", primary=0.8, f1={"refund": 0.9, "ship": 0.9})
    cur = _iteration("itr_CUR", primary=0.8, f1={"refund": 0.5, "ship": 0.9}, iteration=1)
    _seed(db_url, base, cur)
    _capture(capsys, ["--db", db_url, "baseline", "set", WS, "--dataset", DS, "--iteration", base.id])

    rc, out, _ = _capture(
        capsys,
        ["--db", db_url, "regression", "check", WS, "--dataset", DS, "--iteration", cur.id],
    )
    assert rc == 1, out
    assert "f1[refund]" in out or "F1[refund]" in out


def test_regression_check_usage_error_when_no_baseline(
    db_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    cur = _iteration("itr_CUR", primary=0.8)
    _seed(db_url, cur)  # dataset exists, but no baseline set.

    rc, _out, err = _capture(
        capsys,
        ["--db", db_url, "regression", "check", WS, "--dataset", DS, "--iteration", cur.id],
    )
    assert rc == 2
    assert "no baseline" in err


def test_regression_check_usage_error_on_missing_iteration(
    db_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    base = _iteration("itr_BASE", primary=0.8)
    _seed(db_url, base)
    _capture(capsys, ["--db", db_url, "baseline", "set", WS, "--dataset", DS, "--iteration", base.id])

    rc, _out, err = _capture(
        capsys,
        ["--db", db_url, "regression", "check", WS, "--dataset", DS, "--iteration", "itr_MISSING"],
    )
    assert rc == 2
    assert "not found" in err
