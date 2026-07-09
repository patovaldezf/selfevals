"""Tests for arena.worktrees — real git repos in tmp_path, no mocking of git."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from selfevals.arena.worktrees import (
    RefInfo,
    WorktreeError,
    ensure_worktree,
    list_refs,
    prune,
    remove_worktree,
    resolve_ref,
    run_setup,
    worktrees_root,
)


def _run(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(repo, "init", "-q")
    _run(repo, "config", "user.email", "test@example.com")
    _run(repo, "config", "user.name", "Test")
    (repo / "agent.py").write_text("print('v1')\n")
    _run(repo, "add", ".")
    _run(repo, "commit", "-q", "-m", "v1")
    return repo


def test_worktrees_root_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SELFEVALS_WORKTREES_DIR", raising=False)
    assert worktrees_root() == Path.home() / ".selfevals" / "worktrees"
    monkeypatch.setenv("SELFEVALS_WORKTREES_DIR", "/tmp/custom-worktrees")
    assert worktrees_root() == Path("/tmp/custom-worktrees")


def test_resolve_ref_returns_full_sha(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    sha = resolve_ref(str(repo), "HEAD")
    assert len(sha) == 40
    assert resolve_ref(str(repo), "master") == sha or resolve_ref(str(repo), "main") == sha


def test_resolve_ref_rejects_missing_repo(tmp_path: Path) -> None:
    with pytest.raises(WorktreeError, match="does not exist"):
        resolve_ref(str(tmp_path / "nope"), "HEAD")


def test_resolve_ref_rejects_unknown_ref(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    with pytest.raises(WorktreeError, match="could not resolve"):
        resolve_ref(str(repo), "no-such-branch")


def test_ensure_worktree_checks_out_detached_at_sha(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    sha = resolve_ref(str(repo), "HEAD")
    dest = tmp_path / "worktrees" / "variant-a"
    result = ensure_worktree(str(repo), sha, dest)
    assert result == dest
    assert (dest / "agent.py").read_text() == "print('v1')\n"
    head = subprocess.run(
        ["git", "-C", str(dest), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert head == sha


def test_ensure_worktree_is_idempotent_for_same_sha(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    sha = resolve_ref(str(repo), "HEAD")
    dest = tmp_path / "worktrees" / "variant-a"
    ensure_worktree(str(repo), sha, dest)
    marker = dest / "agent.py"
    original_mtime = marker.stat().st_mtime
    ensure_worktree(str(repo), sha, dest)
    assert marker.stat().st_mtime == original_mtime


def test_ensure_worktree_recreates_when_sha_changes(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    sha1 = resolve_ref(str(repo), "HEAD")
    dest = tmp_path / "worktrees" / "variant-a"
    ensure_worktree(str(repo), sha1, dest)

    (repo / "agent.py").write_text("print('v2')\n")
    _run(repo, "add", ".")
    _run(repo, "commit", "-q", "-m", "v2")
    sha2 = resolve_ref(str(repo), "HEAD")
    assert sha2 != sha1

    ensure_worktree(str(repo), sha2, dest)
    assert (dest / "agent.py").read_text() == "print('v2')\n"


def test_run_setup_executes_and_caches(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    sha = resolve_ref(str(repo), "HEAD")
    dest = tmp_path / "worktrees" / "variant-a"
    ensure_worktree(str(repo), sha, dest)

    marker_file = dest / "setup-ran.txt"
    setup_command = ["python3", "-c", f"open({str(marker_file)!r}, 'a').write('x')"]
    run_setup(dest, setup_command)
    assert marker_file.read_text() == "x"

    # Second call is a no-op (cached) — marker content unchanged.
    run_setup(dest, setup_command)
    assert marker_file.read_text() == "x"


def test_run_setup_noop_for_empty_command(tmp_path: Path) -> None:
    dest = tmp_path / "worktrees" / "variant-a"
    dest.mkdir(parents=True)
    run_setup(dest, [])  # must not raise


def test_run_setup_raises_on_nonzero_exit(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    sha = resolve_ref(str(repo), "HEAD")
    dest = tmp_path / "worktrees" / "variant-a"
    ensure_worktree(str(repo), sha, dest)
    with pytest.raises(WorktreeError, match="setup command"):
        run_setup(dest, ["python3", "-c", "import sys; sys.exit(1)"])


def test_remove_and_prune_worktree(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    sha = resolve_ref(str(repo), "HEAD")
    dest = tmp_path / "worktrees" / "variant-a"
    ensure_worktree(str(repo), sha, dest)
    assert dest.exists()
    remove_worktree(str(repo), dest)
    assert not dest.exists()
    prune(str(repo))  # must not raise


def test_remove_worktree_is_noop_when_missing(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    remove_worktree(str(repo), tmp_path / "never-created")  # must not raise


def test_list_refs_returns_branch_and_sha(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    sha = resolve_ref(str(repo), "HEAD")
    _run(repo, "branch", "arena/variant-b")
    refs = list_refs(str(repo))
    names = {r.name for r in refs}
    assert "arena/variant-b" in names
    for ref in refs:
        assert isinstance(ref, RefInfo)
        if ref.name == "arena/variant-b":
            assert ref.sha == sha


def test_list_refs_rejects_missing_repo(tmp_path: Path) -> None:
    with pytest.raises(WorktreeError, match="does not exist"):
        list_refs(str(tmp_path / "nope"))
