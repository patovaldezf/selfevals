"""Tests for `selfevals baseline set/show` and `selfevals regression check`.

Builds IterationRecords directly in storage (no full optimization loop) and
asserts the CLI exit codes (0 ok / 1 regressed / 2 user error) plus the
baseline pointer round-trip.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from selfevals.cli.main import app
from selfevals.graders._confusion import confusion_from_pairs
from selfevals.schemas.baseline import BaselineRecord
from selfevals.schemas.enums import (
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
from selfevals.storage.sqlite import SQLiteStorage
from tests.cli.test_cli import _ws_id_from

EXP = "exp_regression_test"


def _capture(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, str, str]:
    rc = app(argv)
    out = capsys.readouterr()
    return rc, out.out, out.err


def _iteration(
    *,
    ws_id: str,
    experiment_id: str = EXP,
    iteration: int = 0,
    primary: float = 0.8,
    error_rate: float = 0.0,
    confusion_pairs: list[tuple[str, str]] | None = None,
) -> IterationRecord:
    confusion = (
        confusion_from_pairs(confusion_pairs).to_dict() if confusion_pairs is not None else None
    )
    return IterationRecord(
        id=IterationRecord.make_id(),
        workspace_id=ws_id,
        experiment_id=experiment_id,
        iteration=iteration,
        state=IterationState.COMPLETED,
        proposer=ProposerInputs(type=ProposerStrategy.MANUAL),
        hypothesis="h",
        proposed_parameters={"model_params": {"level": 0.0}},
        execution=ExecutionInfo(variant_id="var_x"),
        metrics=IterationMetrics(
            primary=MetricObservation(name="pass@1", value=primary),
            error_rate=error_rate,
            confusion=confusion,
        ),
        decision=IterationDecision(outcome=DecisionOutcome.KEEP_CANDIDATE, rationale="r"),
    )


def _persist(db: Path, ws_id: str, *records: IterationRecord) -> None:
    storage = SQLiteStorage(db)
    try:
        with storage.open(ws_id) as scope:
            for record in records:
                scope.put_entity(record)
    finally:
        storage.close()


def _init_ws(db: Path, capsys: pytest.CaptureFixture[str]) -> str:
    rc, init_out, _ = _capture(capsys, ["--db", str(db), "init", "team"])
    assert rc == 0
    ws_id = _ws_id_from(init_out)
    assert ws_id is not None
    return ws_id


def _baselines(db: Path, ws_id: str) -> list[BaselineRecord]:
    storage = SQLiteStorage(db)
    try:
        with storage.open(ws_id) as scope:
            return [
                r
                for r in scope.list_entities(BaselineRecord)
                if isinstance(r, BaselineRecord)
            ]
    finally:
        storage.close()


def test_baseline_set_picks_best_completed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "db.sqlite"
    ws_id = _init_ws(db, capsys)
    low = _iteration(ws_id=ws_id, iteration=0, primary=0.6)
    high = _iteration(ws_id=ws_id, iteration=1, primary=0.9)
    _persist(db, ws_id, low, high)

    rc, stdout, _ = _capture(capsys, ["--db", str(db), "baseline", "set", ws_id, EXP])
    assert rc == 0
    assert "0.9" in stdout

    records = _baselines(db, ws_id)
    assert len(records) == 1
    assert records[0].iteration_id == high.id
    assert records[0].primary_value == 0.9


def test_baseline_set_explicit_iteration(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "db.sqlite"
    ws_id = _init_ws(db, capsys)
    low = _iteration(ws_id=ws_id, iteration=0, primary=0.6)
    high = _iteration(ws_id=ws_id, iteration=1, primary=0.9)
    _persist(db, ws_id, low, high)

    rc, _, _ = _capture(
        capsys, ["--db", str(db), "baseline", "set", ws_id, EXP, "--iteration", low.id]
    )
    assert rc == 0
    records = _baselines(db, ws_id)
    assert records[0].iteration_id == low.id


def test_baseline_show_reports_current(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "db.sqlite"
    ws_id = _init_ws(db, capsys)
    _persist(db, ws_id, _iteration(ws_id=ws_id, primary=0.85))
    _capture(capsys, ["--db", str(db), "baseline", "set", ws_id, EXP])
    capsys.readouterr()

    rc, stdout, _ = _capture(capsys, ["--db", str(db), "baseline", "show", ws_id, EXP])
    assert rc == 0
    assert "0.85" in stdout


def test_baseline_show_without_baseline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "db.sqlite"
    ws_id = _init_ws(db, capsys)
    rc, stdout, _ = _capture(capsys, ["--db", str(db), "baseline", "show", ws_id, EXP])
    assert rc == 0
    assert "no baseline" in stdout


def test_baseline_set_latest_wins(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "db.sqlite"
    ws_id = _init_ws(db, capsys)
    a = _iteration(ws_id=ws_id, iteration=0, primary=0.6)
    b = _iteration(ws_id=ws_id, iteration=1, primary=0.9)
    _persist(db, ws_id, a, b)
    # First baseline -> a explicitly, then b explicitly; show must report b.
    _capture(capsys, ["--db", str(db), "baseline", "set", ws_id, EXP, "--iteration", a.id])
    _capture(capsys, ["--db", str(db), "baseline", "set", ws_id, EXP, "--iteration", b.id])
    capsys.readouterr()
    rc, stdout, _ = _capture(capsys, ["--db", str(db), "baseline", "show", ws_id, EXP])
    assert rc == 0
    assert b.id in stdout


def test_regression_check_passes_when_stable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "db.sqlite"
    ws_id = _init_ws(db, capsys)
    base = _iteration(ws_id=ws_id, iteration=0, primary=0.8)
    cur = _iteration(ws_id=ws_id, iteration=1, primary=0.8)
    _persist(db, ws_id, base, cur)
    _capture(capsys, ["--db", str(db), "baseline", "set", ws_id, EXP, "--iteration", base.id])
    capsys.readouterr()

    rc, stdout, _ = _capture(
        capsys,
        ["--db", str(db), "regression", "check", ws_id, EXP, "--iteration", cur.id],
    )
    assert rc == 0
    assert "OK" in stdout


def test_regression_check_fails_on_primary_drop(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "db.sqlite"
    ws_id = _init_ws(db, capsys)
    base = _iteration(ws_id=ws_id, iteration=0, primary=0.9)
    cur = _iteration(ws_id=ws_id, iteration=1, primary=0.7)  # drop 0.2
    _persist(db, ws_id, base, cur)
    _capture(capsys, ["--db", str(db), "baseline", "set", ws_id, EXP, "--iteration", base.id])
    capsys.readouterr()

    rc, stdout, _ = _capture(
        capsys,
        ["--db", str(db), "regression", "check", ws_id, EXP, "--iteration", cur.id],
    )
    assert rc == 1
    assert "REGRESSION" in stdout


def test_regression_check_fails_on_per_class_f1_drop(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "db.sqlite"
    ws_id = _init_ws(db, capsys)
    base = _iteration(
        ws_id=ws_id,
        iteration=0,
        primary=0.8,
        confusion_pairs=[("a", "a"), ("a", "a"), ("b", "b"), ("b", "b")],
    )
    cur = _iteration(
        ws_id=ws_id,
        iteration=1,
        primary=0.8,
        confusion_pairs=[("a", "a"), ("a", "a"), ("b", "b"), ("b", "a")],
    )
    _persist(db, ws_id, base, cur)
    _capture(capsys, ["--db", str(db), "baseline", "set", ws_id, EXP, "--iteration", base.id])
    capsys.readouterr()

    rc, stdout, _ = _capture(
        capsys,
        ["--db", str(db), "regression", "check", ws_id, EXP, "--iteration", cur.id],
    )
    assert rc == 1
    assert "f1[b]" in stdout


def test_regression_check_defaults_to_best_iteration(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "db.sqlite"
    ws_id = _init_ws(db, capsys)
    base = _iteration(ws_id=ws_id, iteration=0, primary=0.9)
    # No --iteration: gate picks the best completed, which is base itself (0.9).
    _persist(db, ws_id, base)
    _capture(capsys, ["--db", str(db), "baseline", "set", ws_id, EXP, "--iteration", base.id])
    capsys.readouterr()
    rc, stdout, _ = _capture(capsys, ["--db", str(db), "regression", "check", ws_id, EXP])
    assert rc == 0
    assert "OK" in stdout


def test_regression_check_without_baseline_is_user_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "db.sqlite"
    ws_id = _init_ws(db, capsys)
    _persist(db, ws_id, _iteration(ws_id=ws_id, primary=0.8))
    rc, _, stderr = _capture(capsys, ["--db", str(db), "regression", "check", ws_id, EXP])
    assert rc == 2
    assert "no baseline" in stderr
