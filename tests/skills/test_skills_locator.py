"""The bundled skills locator: error-analysis ships and is discoverable, and an
unknown skill raises a clear KeyError (design §8)."""

from __future__ import annotations

import pytest

from selfeval import skills


def test_error_analysis_is_bundled() -> None:
    assert "error-analysis" in skills.list_skills()


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
