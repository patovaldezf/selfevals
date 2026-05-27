from __future__ import annotations

from pathlib import Path

import pytest

from selfeval.cli.main import app


def _capture(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, str, str]:
    rc = app(argv)
    out = capsys.readouterr()
    return rc, out.out, out.err


def test_examples_copy_pingpong_writes_runnable_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, stdout, _ = _capture(capsys, ["examples", "copy", "pingpong", "--to", str(tmp_path)])
    assert rc == 0
    assert "copied example 'pingpong'" in stdout
    assert (tmp_path / "evals" / "experiments" / "example_pingpong.yaml").is_file()
    assert (tmp_path / "evals" / "datasets" / "pingpong.jsonl").is_file()


def test_examples_copy_refuses_to_overwrite(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _capture(capsys, ["examples", "copy", "pingpong", "--to", str(tmp_path)])
    rc, _, stderr = _capture(capsys, ["examples", "copy", "pingpong", "--to", str(tmp_path)])
    assert rc == 2
    assert "refusing to overwrite" in stderr
