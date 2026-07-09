"""Git worktree plumbing for Arena variants.

Pure module — no storage access, no knowledge of `Arena`/`ArenaVariant`
entities. Everything here shells out to `git` via `subprocess.run` (never
`shell=True`) against a `repo_path` the caller already validated.

Reproducibility: a variant is checked out **detached at a resolved commit
SHA**, not tracking the branch tip. `resolve_ref` pins the SHA at
registration time, so a variant's results stay attributable to one exact
commit even if the source branch keeps moving — and a dirty main working
tree never leaks into a worktree checkout.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from selfevals._errors import SelfEvalsUserError

_GIT_TIMEOUT_SECONDS = 30.0


class WorktreeError(SelfEvalsUserError):
    """Raised when a git worktree operation fails for a user-correctable reason."""


@dataclass(frozen=True)
class RefInfo:
    """One ref returned by `list_refs`, for a branch picker UI."""

    name: str
    sha: str


def worktrees_root() -> Path:
    """Base directory Arena worktrees are checked out under.

    Override with `SELFEVALS_WORKTREES_DIR`; defaults to
    `~/.selfevals/worktrees`, kept outside any single repo so it survives
    `git clean` and is trivially excluded from the user's own `.gitignore`.
    """
    override = os.environ.get("SELFEVALS_WORKTREES_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".selfevals" / "worktrees"


def _run_git(repo_path: str, args: list[str], *, timeout: float = _GIT_TIMEOUT_SECONDS) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", repo_path, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise WorktreeError("git executable not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise WorktreeError(f"git {' '.join(args)} timed out after {timeout}s") from exc
    if result.returncode != 0:
        raise WorktreeError(
            f"git {' '.join(args)} failed in {repo_path!r}: {result.stderr.strip()}"
        )
    return result.stdout.strip()


def resolve_ref(repo_path: str, ref: str) -> str:
    """Resolve `ref` (branch, tag, or commit-ish) to a full commit SHA.

    Raises `WorktreeError` if `repo_path` is not a git repo or `ref` does
    not resolve to a commit — surfaced to the caller (Arena's
    `register_variant`) before any worktree is created.
    """
    if not Path(repo_path).is_dir():
        raise WorktreeError(f"repo_path does not exist or is not a directory: {repo_path!r}")
    try:
        return _run_git(repo_path, ["rev-parse", "--verify", f"{ref}^{{commit}}"])
    except WorktreeError as exc:
        raise WorktreeError(f"could not resolve git ref {ref!r} in {repo_path!r}: {exc}") from exc


def _setup_marker(worktree: Path, setup_command: list[str]) -> Path:
    digest = hashlib.sha256(" ".join(setup_command).encode("utf-8")).hexdigest()[:16]
    return worktree / f".selfevals-setup-{digest}.ok"


def ensure_worktree(repo_path: str, sha: str, dest: Path) -> Path:
    """Create (or reuse) a detached worktree at `dest`, checked out at `sha`.

    Idempotent: if `dest` already exists and its HEAD matches `sha`, it is
    reused untouched (setup cache stays valid). If `dest` exists but points
    at a different commit, it is removed and recreated — this only happens
    when a variant is re-registered against a moved ref under the same
    variant id, which the service layer avoids by minting a fresh variant id
    per registration; treated here as a safety net, not the common path.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        try:
            current = _run_git(str(dest), ["rev-parse", "HEAD"])
        except WorktreeError:
            current = None
        if current == sha:
            return dest
        remove_worktree(repo_path, dest)
    _run_git(repo_path, ["worktree", "add", "--detach", str(dest), sha], timeout=120.0)
    return dest


def run_setup(worktree: Path, setup_command: list[str], *, timeout: float = 600.0) -> None:
    """Run a variant's setup command (e.g. `uv sync`) inside its worktree.

    Cached by a marker file keyed on the command itself: re-registering the
    same variant (same worktree, unchanged setup_command) skips re-running
    setup. A changed setup_command hashes differently and reruns.
    """
    if not setup_command:
        return
    marker = _setup_marker(worktree, setup_command)
    if marker.exists():
        return
    try:
        result = subprocess.run(
            setup_command,
            cwd=worktree,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise WorktreeError(f"setup command executable not found: {setup_command[0]!r}") from exc
    except subprocess.TimeoutExpired as exc:
        raise WorktreeError(f"setup command timed out after {timeout}s: {setup_command}") from exc
    if result.returncode != 0:
        raise WorktreeError(
            f"setup command {setup_command} failed in {worktree}: {result.stderr.strip()}"
        )
    marker.write_text("ok")


def remove_worktree(repo_path: str, dest: Path) -> None:
    """Remove a worktree checkout. Safe to call on an already-removed path."""
    if not dest.exists():
        return
    _run_git(repo_path, ["worktree", "remove", "--force", str(dest)])


def prune(repo_path: str) -> None:
    """Clean up git's internal worktree bookkeeping for removed directories."""
    _run_git(repo_path, ["worktree", "prune"])


def list_refs(repo_path: str) -> list[RefInfo]:
    """List local branches for the Arena variant picker (name + current SHA)."""
    if not Path(repo_path).is_dir():
        raise WorktreeError(f"repo_path does not exist or is not a directory: {repo_path!r}")
    output = _run_git(
        repo_path,
        ["for-each-ref", "refs/heads", "--format=%(refname:short) %(objectname)"],
    )
    refs: list[RefInfo] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        name, _, sha = line.partition(" ")
        refs.append(RefInfo(name=name, sha=sha))
    return refs
