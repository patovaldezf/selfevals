"""`selfevals skills list` lists bundled skills; `skills path` prints a skill's
directory and exits 2 (SelfEvalsUserError) on an unknown name."""

from __future__ import annotations

import pytest

from selfevals.cli.main import app


def _capture(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, str]:
    rc = app(argv)
    return rc, capsys.readouterr().out


def test_skills_list_includes_error_analysis(capsys: pytest.CaptureFixture[str]) -> None:
    rc, out = _capture(capsys, ["skills", "list"])
    assert rc == 0
    assert "error-analysis" in out


def test_skills_path_prints_directory(capsys: pytest.CaptureFixture[str]) -> None:
    rc, out = _capture(capsys, ["skills", "path", "error-analysis"])
    assert rc == 0
    assert out.strip().endswith("error-analysis")


def test_skills_path_unknown_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    rc = app(["skills", "path", "nope"])
    assert rc == 2
