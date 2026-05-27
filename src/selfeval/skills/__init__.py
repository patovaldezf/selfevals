"""Locate the agent skills bundled with the selfeval SDK.

Skills ship inside the installed package under `selfeval/.agents/skills/<name>/`
(the same convention FastAPI uses). A coding agent — or the onboarding flow that
installs them into a project's skill directory — finds them through here rather
than guessing at an install path, so it works identically from a wheel, an
editable install, or the source tree.

selfeval owns the *method* encoded in each skill; the agent supplies the
intelligence when it runs one. See docs/spec/error_analysis_design.md §8.
"""

from __future__ import annotations

from importlib import resources
from importlib.resources.abc import Traversable

# Anchor on the top-level package, then descend — avoids relying on ".." over a
# Traversable, which isn't defined for zip-backed (zipimport) installs.
_PACKAGE_ANCHOR = "selfeval"
_SKILLS_SUBDIR = (".agents", "skills")


def _skills_root() -> Traversable:
    """The `selfeval/.agents/skills` directory inside the installed package."""
    root = resources.files(_PACKAGE_ANCHOR)
    for part in _SKILLS_SUBDIR:
        root = root.joinpath(part)
    return root


def list_skills() -> list[str]:
    """Names of the skills bundled with this install, sorted.

    A skill is any subdirectory of `.agents/skills` containing a `SKILL.md`.
    """
    root = _skills_root()
    if not root.is_dir():
        return []
    names = [
        entry.name
        for entry in root.iterdir()
        if entry.is_dir() and entry.joinpath("SKILL.md").is_file()
    ]
    return sorted(names)


def skill_path(name: str) -> Traversable:
    """The directory of the named bundled skill.

    Raises `KeyError` if no such skill ships with this install. The returned
    `Traversable` can be read directly or copied into a project's skill folder.
    """
    skill_dir = _skills_root().joinpath(name)
    if not skill_dir.is_dir() or not skill_dir.joinpath("SKILL.md").is_file():
        available = ", ".join(list_skills()) or "(none)"
        raise KeyError(f"no bundled skill named {name!r}; available: {available}")
    return skill_dir


__all__ = ["list_skills", "skill_path"]
