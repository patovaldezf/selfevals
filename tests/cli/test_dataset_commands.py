"""CLI tests for `selfevals dataset` — standalone dataset lifecycle.

These exercise the full create → list → show → freeze flow through `app()`,
asserting a dataset is persisted without ever launching an experiment.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from selfevals.cli.main import app

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _capture(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, str, str]:
    rc = app(argv)
    out = capsys.readouterr()
    return rc, out.out, out.err


def _case_row() -> dict:
    return {
        "name": "say pong",
        "task_type": "echo",
        "input": {"messages": [{"role": "user", "content": "ping"}]},
        "taxonomy": {
            "level": "final_response",
            "feature": {"primary": "commerce.product_resolution"},
            "source": {"type": "handcrafted"},
            "ground_truth": {"methods": ["exact_match"]},
            "dataset_type": "capability",
        },
        "expected": {"must_include": ["pong"]},
    }


def _jsonl(tmp_path: Path, n: int = 2) -> Path:
    p = tmp_path / "cases.jsonl"
    p.write_text("\n".join(json.dumps(_case_row()) for _ in range(n)) + "\n")
    return p


def _create(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], *, db: str, ttype: str = "capability"
) -> str:
    jsonl = _jsonl(tmp_path)
    rc, out, _ = _capture(
        capsys,
        [
            "--db",
            db,
            "dataset",
            "create",
            WS,
            "--from",
            str(jsonl),
            "--name",
            "golden-v1",
            "--type",
            ttype,
        ],
    )
    assert rc == 0, out
    # `created dataset id=ds_...`
    dataset_id = out.splitlines()[0].split("id=", 1)[1].strip()
    assert dataset_id.startswith("ds_")
    return dataset_id


def test_dataset_create_persists_without_experiment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], db_url: str
) -> None:
    dataset_id = _create(tmp_path, capsys, db=db_url)

    rc, out, _ = _capture(capsys, ["--db", db_url, "dataset", "list", WS])
    assert rc == 0
    assert dataset_id in out
    assert "status=active" in out
    assert "cases=2" in out


def test_dataset_show_reports_statistics(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], db_url: str
) -> None:
    dataset_id = _create(tmp_path, capsys, db=db_url)

    rc, out, _ = _capture(capsys, ["--db", db_url, "dataset", "show", WS, dataset_id])
    assert rc == 0
    assert "manifest_hash: sha256:" in out
    assert "total_cases:   2" in out
    assert "commerce.product_resolution=2" in out


def test_dataset_freeze_sets_status(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], db_url: str
) -> None:
    dataset_id = _create(tmp_path, capsys, db=db_url)

    rc, out, _ = _capture(capsys, ["--db", db_url, "dataset", "freeze", WS, dataset_id])
    assert rc == 0
    assert "status=frozen" in out

    rc, out, _ = _capture(capsys, ["--db", db_url, "dataset", "list", WS])
    assert "status=frozen" in out


def test_dataset_list_status_filter(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], db_url: str
) -> None:
    dataset_id = _create(tmp_path, capsys, db=db_url)

    rc, out, _ = _capture(capsys, ["--db", db_url, "dataset", "list", WS, "--status", "frozen"])
    assert rc == 0
    assert dataset_id not in out
    assert "(no datasets)" in out


def test_dataset_create_missing_file_is_user_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], db_url: str
) -> None:
    rc, _out, err = _capture(
        capsys,
        [
            "--db",
            db_url,
            "dataset",
            "create",
            WS,
            "--from",
            str(tmp_path / "nope.jsonl"),
            "--name",
            "x",
        ],
    )
    assert rc == 2
    assert "not found" in err.lower()


def test_dataset_show_unknown_id_is_user_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], db_url: str
) -> None:
    _create(tmp_path, capsys, db=db_url)
    rc, _out, err = _capture(
        capsys, ["--db", db_url, "dataset", "show", WS, "ds_does_not_exist"]
    )
    assert rc == 2
    assert "not found" in err.lower()
