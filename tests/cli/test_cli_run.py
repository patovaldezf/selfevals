from __future__ import annotations

import json
from pathlib import Path

import pytest

from selfevals.cli.main import app

REPO_EXAMPLE = (
    Path(__file__).resolve().parents[2] / "evals" / "experiments" / "example_pingpong.yaml"
)


def _capture(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, str, str]:
    rc = app(argv)
    out = capsys.readouterr()
    return rc, out.out, out.err


def test_run_example_no_persist_markdown(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, stdout, _ = _capture(
        capsys,
        ["run", str(REPO_EXAMPLE), "--no-persist", "--max-iterations", "2"],
    )
    assert rc == 0
    assert "# Experiment: pingpong baseline" in stdout
    # Grid sweeps level=0 then level=1; the best iteration must hit pass@1=1.
    assert "pass@1 = 1" in stdout


def test_run_example_no_persist_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc, stdout, _ = _capture(
        capsys,
        [
            "run",
            str(REPO_EXAMPLE),
            "--no-persist",
            "--max-iterations",
            "2",
            "--format",
            "json",
        ],
    )
    assert rc == 0
    payload = json.loads(stdout)
    assert payload["experiment"]["name"] == "pingpong baseline"
    assert payload["termination"]["iterations_run"] == 2
    assert payload["best_iteration"]["metrics"]["primary"]["value"] == 1.0


def test_run_persists_iterations_to_storage(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "db.sqlite"
    rc, _, _ = _capture(
        capsys,
        ["--db", str(db), "run", str(REPO_EXAMPLE), "--max-iterations", "2"],
    )
    assert rc == 0
    # IterationRecords + DecisionRecords should be in the db.
    from selfevals.schemas.iteration import DecisionRecord, IterationRecord
    from selfevals.storage.sqlite import SQLiteStorage

    storage = SQLiteStorage(db)
    try:
        with storage.open("ws_01HZZZZZZZZZZZZZZZZZZZZZZZ") as scope:
            iters = scope.list_entities(IterationRecord)
            decisions = scope.list_entities(DecisionRecord)
    finally:
        storage.close()
    assert len(iters) == 2
    assert len(decisions) == 2


def test_run_missing_spec_reports_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc, _, stderr = _capture(
        capsys,
        ["run", str(tmp_path / "nope.yaml"), "--no-persist"],
    )
    assert rc == 2
    assert "not found" in stderr


def test_run_invalid_max_iterations(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc, _, stderr = _capture(
        capsys,
        ["run", str(REPO_EXAMPLE), "--no-persist", "--max-iterations", "0"],
    )
    assert rc == 2
    assert ">= 1" in stderr


def test_run_user_callable_returning_str_is_wrapped(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Spec that points to a stdlib function returning a str — we expect
    # the loader to wrap it as AdapterResponse.
    yaml_path = tmp_path / "exp.yaml"
    yaml_path.write_text(
        """
workspace: ws_01HZZZZZZZZZZZZZZZZZZZZZZZ
experiment:
  name: str-return demo
  goal: verify str→AdapterResponse coercion
  mode: handoff
  taxonomy:
    target_features: [commerce.product_resolution]
    dataset_types: [capability]
  datasets:
    optimization: { id: ds_x, version: 1 }
  target:
    primary: { name: pass@1, operator: ">=", value: 0.5 }
  editable:
    prompt: true
    model_params: true
  frozen:
    fleet: { id: flt_x }
    agents: [{ id: ag_x }]
    datasets: [{ id: ds_x }]
  proposer:
    strategy: grid
  search_space:
    model_params:
      x: [1]
  run:
    sandbox: mock
    max_iterations: 1
    convergence: { min_delta: 1.0e-6, patience: 10 }
  reliability:
    metrics: [pass@1]
dataset:
  cases_inline:
    - name: t
      task_type: x
      input: { messages: [{ role: user, content: hi }] }
      taxonomy:
        level: final_response
        feature: { primary: commerce.product_resolution }
        source: { type: handcrafted }
        ground_truth: { methods: [exact_match] }
        dataset_type: capability
      expected: { must_include: [pong] }
agent:
  entrypoint: tests.cli.helpers_str_agent:run
"""
    )
    rc, stdout, stderr = _capture(
        capsys, ["run", str(yaml_path), "--no-persist", "--format", "json"]
    )
    assert rc == 0, stderr
    payload = json.loads(stdout)
    # The fake agent always returns "pong", so primary should be 1.0.
    assert payload["iterations"][0]["metrics"]["primary"]["value"] == 1.0


_FAILING_SPEC = """
workspace: ws_01HZZZZZZZZZZZZZZZZZZZZZZZ
experiment:
  name: failing demo
  goal: produce a failing grade so failure_reasons is non-empty
  mode: handoff
  taxonomy:
    target_features: [commerce.product_resolution]
    dataset_types: [capability]
  datasets:
    optimization: { id: ds_x, version: 1 }
  target:
    primary: { name: pass@1, operator: ">=", value: 0.5 }
  editable:
    prompt: true
    model_params: true
  frozen:
    fleet: { id: flt_x }
    agents: [{ id: ag_x }]
    datasets: [{ id: ds_x }]
  proposer:
    strategy: grid
  search_space:
    model_params:
      x: [1]
  run:
    sandbox: mock
    max_iterations: 1
    persist_traces: failed
    convergence: { min_delta: 1.0e-6, patience: 10 }
  reliability:
    metrics: [pass@1]
dataset:
  cases_inline:
    - name: t
      task_type: x
      input: { messages: [{ role: user, content: hi }] }
      taxonomy:
        level: final_response
        feature: { primary: commerce.product_resolution }
        source: { type: handcrafted }
        ground_truth: { methods: [exact_match] }
        dataset_type: capability
      expected: { must_include: [WONTMATCH] }
      graders: [rules]
graders:
  - type: deterministic
    name: rules
agent:
  entrypoint: tests.cli.helpers_str_agent:run
"""


def test_report_rehydrates_failure_reasons_from_storage(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The agent returns "pong" but the case requires "WONTMATCH" → the
    # deterministic grader FAILs with a reason. With persist_traces=failed
    # that trace (and its grader_results) is written to storage. `report`
    # rebuilds the IterationOutcome from disk: it must reload the traces so
    # failure_reasons matches an inline `run --format json`, not come back [].
    yaml_path = tmp_path / "exp.yaml"
    yaml_path.write_text(_FAILING_SPEC)
    db = tmp_path / "db.sqlite"

    # 1. Inline run (same process) — baseline for what failure_reasons should be.
    rc, inline_out, err = _capture(
        capsys, ["--db", str(db), "run", str(yaml_path), "--format", "json"]
    )
    assert rc == 0, err
    inline = json.loads(inline_out)
    inline_reasons = inline["iterations"][0]["failure_reasons"]
    assert inline_reasons, "inline run should surface the failing grade's reason"
    exp_id = inline["experiment"]["id"]

    # 2. Separate report from storage — must rehydrate the same reasons.
    rc, report_out, err = _capture(
        capsys,
        ["--db", str(db), "report", "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ", exp_id, "--format", "json"],
    )
    assert rc == 0, err
    report = json.loads(report_out)
    report_reasons = report["iterations"][0]["failure_reasons"]
    assert report_reasons, "report rebuilt from storage must repopulate failure_reasons (not [])"
    # Same failing grader + reason survives the round-trip.
    inline_keys = {(r["grader"], r["label"], r["reason"]) for r in inline_reasons}
    report_keys = {(r["grader"], r["label"], r["reason"]) for r in report_reasons}
    assert report_keys == inline_keys
