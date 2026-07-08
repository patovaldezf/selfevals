"""`selfevals skills list` lists bundled skills; `skills path` prints a skill's
directory and exits 2 (SelfEvalsUserError) on an unknown name; `skills sync`
installs the consumer skills into a destination and is idempotent; the on-invoke
auto-sync respects the opt-out env var."""

from __future__ import annotations

from pathlib import Path

import pytest

from selfevals import skills
from selfevals.cli.main import app


@pytest.fixture(autouse=True)
def _no_autosync(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable the on-invoke auto-sync by default so a bare `app(...)` in a test
    never writes skill files into the repo. Tests that exercise auto-sync opt
    back in explicitly."""
    monkeypatch.setenv("SELFEVALS_NO_SKILL_SYNC", "1")


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


def test_skills_sync_installs_consumer_skills(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    dest = tmp_path / "skills"
    rc, _ = _capture(capsys, ["skills", "sync", "--to", str(dest)])
    assert rc == 0
    # Every consumer skill landed with its SKILL.md.
    for name in skills.CONSUMER_SKILLS:
        assert (dest / name / "SKILL.md").is_file()
    # Maintenance skills are not part of the consumer sync.
    assert not (dest / "selfevals-change-guard").exists()


def test_skills_sync_is_idempotent(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    dest = tmp_path / "skills"
    app(["skills", "sync", "--to", str(dest)])
    capsys.readouterr()
    rc, out = _capture(capsys, ["skills", "sync", "--to", str(dest)])
    assert rc == 0
    assert "already up to date" in out


def test_skills_sync_all_includes_maintenance(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    dest = tmp_path / "skills"
    rc, _ = _capture(capsys, ["skills", "sync", "--to", str(dest), "--all"])
    assert rc == 0
    assert (dest / "selfevals-change-guard" / "SKILL.md").is_file()


def test_autosync_opt_out_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A project dir (has .claude) but the opt-out env is set → no skills written.
    (tmp_path / ".claude").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SELFEVALS_NO_SKILL_SYNC", "1")
    app(["skills", "list"])
    assert not (tmp_path / ".claude" / "skills").exists()


def test_autosync_populates_project_skills(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A project dir with the opt-out cleared → auto-sync installs consumer skills.
    (tmp_path / ".claude").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SELFEVALS_NO_SKILL_SYNC", raising=False)
    app(["skills", "list"])
    installed = {p.name for p in (tmp_path / ".claude" / "skills").iterdir()}
    assert set(skills.CONSUMER_SKILLS) <= installed
    assert "selfevals-change-guard" not in installed
