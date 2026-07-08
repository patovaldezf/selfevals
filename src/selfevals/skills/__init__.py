"""Locate the agent skills bundled with the selfevals SDK.

Skills ship inside the installed package under `selfevals/.agents/skills/<name>/`
(the same convention FastAPI uses). A coding agent — or the onboarding flow that
installs them into a project's skill directory — finds them through here rather
than guessing at an install path, so it works identically from a wheel, an
editable install, or the source tree.

selfevals owns the *method* encoded in each skill; the agent supplies the
intelligence when it runs one. See docs/spec/error_analysis_design.md §8.
"""

from __future__ import annotations

from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

# Anchor on the top-level package, then descend — avoids relying on ".." over a
# Traversable, which isn't defined for zip-backed (zipimport) installs.
_PACKAGE_ANCHOR = "selfevals"
_SKILLS_SUBDIR = (".agents", "skills")

# The "consumer" skills: how to *use* selfevals to evaluate an agent. These are
# what a coding agent in another project wants — so these (and only these) are
# what `sync_skills` installs into a project's skill directory by default. The
# `selfevals-*-change` skills are guardrails for hacking on *this* repo; a
# consumer of the framework never wants them, so they're excluded from the sync.
CONSUMER_SKILLS = (
    "evaluate-this-repo",
    "selfevals",
    "design-your-dataset",
    "connect-your-agent",
    "run-eval-experiment",
    "error-analysis",
    "iterate-and-ship",
    "arena-iterate",
)

# Default place a project keeps its agent skills (the convention Claude Code and
# the agents/openai loaders look in). `sync_skills` writes here unless told otherwise.
DEFAULT_SKILL_DEST = Path(".claude") / "skills"


def _skills_root() -> Traversable:
    """The `selfevals/.agents/skills` directory inside the installed package."""
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


def _copy_tree_if_changed(src: Traversable, dest: Path) -> list[Path]:
    """Recursively copy a bundled skill directory to disk, overwriting only files
    whose bytes differ.

    Idempotent by content: re-running is a no-op once everything matches, so the
    auto-sync on every CLI invocation stays cheap and never churns a project's
    skill files (or their git status) when nothing changed. Reads bytes (not
    text) so it works the same for a wheel/zip-backed install as for the source
    tree.
    """
    written: list[Path] = []
    for entry in src.iterdir():
        target = dest / entry.name
        if entry.is_dir():
            written.extend(_copy_tree_if_changed(entry, target))
            continue
        new = entry.read_bytes()
        if target.exists() and target.read_bytes() == new:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(new)
        written.append(target)
    return written


def sync_skills(
    dest: Path | str = DEFAULT_SKILL_DEST,
    *,
    only_consumer: bool = True,
) -> list[Path]:
    """Install the bundled skills into a project's skill directory.

    This is how the skills reach a coding agent: the wheel can't run a
    post-install hook, so the CLI calls this lazily (see `cli.main`) and a
    `selfevals skills sync` exposes it explicitly. Copies each skill's full tree
    (SKILL.md plus any `agents/` files) into ``dest/<name>/``.

    With ``only_consumer=True`` (default) it installs just `CONSUMER_SKILLS` — the
    skills for *using* the framework — and leaves the repo-maintenance
    `selfevals-*-change` skills out; a consumer never wants those. Returns the
    list of files actually written (empty when everything was already current),
    so callers can stay silent on a no-op.
    """
    dest_path = Path(dest)
    names = CONSUMER_SKILLS if only_consumer else tuple(list_skills())
    written: list[Path] = []
    for name in names:
        try:
            src = skill_path(name)
        except KeyError:
            # A name in CONSUMER_SKILLS that isn't bundled in this install is a
            # packaging slip, not a user error — skip it rather than abort the
            # whole sync (and the CLI command that triggered it).
            continue
        written.extend(_copy_tree_if_changed(src, dest_path / name))
    return written


__all__ = [
    "CONSUMER_SKILLS",
    "DEFAULT_SKILL_DEST",
    "list_skills",
    "skill_path",
    "sync_skills",
]
