"""The bundled skills locator: the consumer skills ship and are discoverable, an
unknown skill raises a clear KeyError (design §8), and `sync_skills` installs the
consumer set (not the repo-maintenance skills) idempotently."""

from __future__ import annotations

from pathlib import Path

import pytest

from selfevals import skills


def test_error_analysis_is_bundled() -> None:
    assert "error-analysis" in skills.list_skills()


def test_all_consumer_skills_are_bundled() -> None:
    bundled = set(skills.list_skills())
    missing = set(skills.CONSUMER_SKILLS) - bundled
    assert not missing, f"consumer skills not bundled: {sorted(missing)}"


def test_skill_path_points_at_a_readable_skill_md() -> None:
    path = skills.skill_path("error-analysis")
    skill_md = path.joinpath("SKILL.md")
    assert skill_md.is_file()
    text = skill_md.read_text()
    # The skill encodes the method, not intelligence — it must cite the coding
    # technique and the pull/push handshake.
    assert "open coding" in text.lower()
    assert "axial coding" in text.lower()
    assert "analyze pull" in text
    assert "analyze push" in text


def test_unknown_skill_raises_keyerror_listing_available() -> None:
    with pytest.raises(KeyError) as excinfo:
        skills.skill_path("does-not-exist")
    assert "error-analysis" in str(excinfo.value)


def test_sync_skills_installs_only_consumer(tmp_path: Path) -> None:
    dest = tmp_path / "skills"
    written = skills.sync_skills(dest)
    assert written  # first sync writes files
    installed = {p.name for p in dest.iterdir()}
    assert set(skills.CONSUMER_SKILLS) <= installed
    # The repo-maintenance skills are excluded from the consumer sync.
    assert not any(name.startswith("selfevals-") and name.endswith("-change") for name in installed)


def test_sync_skills_is_idempotent_by_content(tmp_path: Path) -> None:
    dest = tmp_path / "skills"
    skills.sync_skills(dest)
    # A second sync with nothing changed writes nothing.
    assert skills.sync_skills(dest) == []


def test_sync_skills_all_includes_maintenance(tmp_path: Path) -> None:
    dest = tmp_path / "skills"
    skills.sync_skills(dest, only_consumer=False)
    installed = {p.name for p in dest.iterdir()}
    assert "selfevals-change-guard" in installed
