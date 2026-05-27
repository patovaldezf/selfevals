"""Tests for `selfevals compare` CLI + the underlying render_compare."""

from __future__ import annotations

from pathlib import Path

import pytest

from selfevals.cli.main import app
from selfevals.reporter.compare import render_compare
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
from tests.cli.test_cli import _seed_experiment_into_db, _ws_id_from

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _capture(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, str, str]:
    rc = app(argv)
    out = capsys.readouterr()
    return rc, out.out, out.err


def _load_iterations(db: Path, ws_id: str) -> list[IterationRecord]:
    """Load every IterationRecord in `ws_id` sorted by iteration index."""
    storage = SQLiteStorage(db)
    try:
        with storage.open(ws_id) as scope:
            entities = scope.list_entities(IterationRecord)
            iterations = [it for it in entities if isinstance(it, IterationRecord)]
    finally:
        storage.close()
    iterations.sort(key=lambda it: it.iteration)
    return iterations


def _iteration_record(
    *,
    iteration: int = 0,
    parameters: dict[str, object] | None = None,
    primary: float = 0.5,
    guardrails: dict[str, float] | None = None,
    outcome: DecisionOutcome = DecisionOutcome.KEEP_CANDIDATE,
) -> IterationRecord:
    return IterationRecord(
        id=IterationRecord.make_id(),
        workspace_id=WS,
        experiment_id="exp_x",
        iteration=iteration,
        state=IterationState.COMPLETED,
        proposer=ProposerInputs(type=ProposerStrategy.MANUAL),
        hypothesis="h",
        proposed_parameters=parameters or {"model_params": {"level": 0.0}},
        execution=ExecutionInfo(variant_id="var_x"),
        metrics=IterationMetrics(
            primary=MetricObservation(name="pass@1", value=primary),
            guardrails=[
                MetricObservation(name=name, value=val) for name, val in (guardrails or {}).items()
            ],
        ),
        decision=IterationDecision(outcome=outcome, rationale="r"),
    )


def test_render_compare_header_and_tables() -> None:
    a = _iteration_record(iteration=0, primary=0.2)
    b = _iteration_record(
        iteration=3,
        parameters={"model_params": {"level": 1.0}},
        primary=0.8,
    )
    out = render_compare(a, b)
    assert "Comparing iter A (#0) vs iter B (#3)" in out
    assert f"`{a.id}`" in out
    assert f"`{b.id}`" in out
    assert "## Proposal diff" in out
    assert "## Metrics diff" in out


def test_render_compare_proposal_diff_marks_changed_params() -> None:
    a = _iteration_record(parameters={"model_params": {"level": 0.0, "shared": 1}})
    b = _iteration_record(parameters={"model_params": {"level": 1.0, "shared": 1}})
    out = render_compare(a, b)
    # The diff flattens nested params and marks differences.
    assert "model_params.level" in out
    assert "model_params.shared" in out
    # Only the changed row gets the "yes" marker.
    changed_lines = [line for line in out.splitlines() if "model_params.level" in line]
    assert any("yes" in line for line in changed_lines)
    unchanged_lines = [line for line in out.splitlines() if "model_params.shared" in line]
    assert all("yes" not in line for line in unchanged_lines)


def test_render_compare_metrics_diff_includes_delta() -> None:
    a = _iteration_record(primary=0.4, guardrails={"cost_usd_per_case": 0.10})
    b = _iteration_record(primary=0.7, guardrails={"cost_usd_per_case": 0.12})
    out = render_compare(a, b)
    assert "`pass@1`" in out
    assert "`cost_usd_per_case`" in out
    # pass@1 delta is +0.3
    assert "+0.3" in out


def test_render_compare_recommendation_when_b_wins() -> None:
    a = _iteration_record(primary=0.2)
    b = _iteration_record(primary=0.9)
    out = render_compare(a, b)
    assert "## Recommendation" in out
    assert "B is better" in out
    assert "+0.7" in out


def test_render_compare_recommendation_when_a_wins() -> None:
    a = _iteration_record(primary=0.9)
    b = _iteration_record(primary=0.2)
    out = render_compare(a, b)
    assert "A is better" in out
    assert "-0.7" in out


def test_render_compare_recommendation_tie() -> None:
    a = _iteration_record(primary=0.5)
    b = _iteration_record(primary=0.5)
    out = render_compare(a, b)
    assert "tie" in out


def test_render_compare_different_primary_metrics_warns() -> None:
    a = _iteration_record(primary=0.5)
    b = _iteration_record(primary=0.5)
    # Forcing a different primary name on b.
    b_alt = b.model_copy(
        update={"metrics": IterationMetrics(primary=MetricObservation(name="recall@5", value=0.8))}
    )
    out = render_compare(a, b_alt)
    assert "different primary metrics" in out


def test_cli_compare_shows_diff_tables(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "db.sqlite"
    rc, init_out, _ = _capture(capsys, ["--db", str(db), "init", "team"])
    assert rc == 0
    ws_id = _ws_id_from(init_out)
    assert ws_id is not None
    _seed_experiment_into_db(db, ws_id)

    iterations = _load_iterations(db, ws_id)
    assert len(iterations) == 2

    rc, stdout, _ = _capture(
        capsys,
        ["--db", str(db), "compare", ws_id, iterations[0].id, iterations[1].id],
    )
    assert rc == 0
    # Header
    assert "Comparing iter A (#0) vs iter B (#1)" in stdout
    # Proposal diff
    assert "## Proposal diff" in stdout
    assert "model_params.level" in stdout
    # Metrics diff with delta
    assert "## Metrics diff" in stdout
    assert "Δ" in stdout
    # Recommendation: B (level=1.0) clearly wins over A (level=0.0).
    assert "B is better" in stdout


def test_cli_compare_rejects_iterations_from_different_experiments(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "db.sqlite"
    rc, init_out, _ = _capture(capsys, ["--db", str(db), "init", "team"])
    assert rc == 0
    ws_id = _ws_id_from(init_out)
    assert ws_id is not None
    _seed_experiment_into_db(db, ws_id)
    all_iters = _load_iterations(db, ws_id)
    _seed_experiment_into_db(db, ws_id)
    all_after = _load_iterations(db, ws_id)
    # Pick one from experiment A and one from experiment B.
    exp_a_id = all_iters[0].experiment_id
    other = next(it for it in all_after if it.experiment_id != exp_a_id)

    rc, _, stderr = _capture(
        capsys,
        ["--db", str(db), "compare", ws_id, all_iters[0].id, other.id],
    )
    assert rc == 2
    assert "different experiments" in stderr


def test_cli_compare_unknown_iteration_id_reports_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "db.sqlite"
    rc, init_out, _ = _capture(capsys, ["--db", str(db), "init", "team"])
    assert rc == 0
    ws_id = _ws_id_from(init_out)
    assert ws_id is not None
    rc, _, stderr = _capture(
        capsys,
        [
            "--db",
            str(db),
            "compare",
            ws_id,
            "itr_01XXXXXXXXXXXXXXXXXXXXXXXX",
            "itr_01YYYYYYYYYYYYYYYYYYYYYYYY",
        ],
    )
    assert rc == 2
    assert "not found" in stderr
