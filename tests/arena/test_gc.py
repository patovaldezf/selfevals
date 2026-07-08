"""Tests for arena.service.gc_orphaned_worktrees — reconciles the shared
worktrees directory against every workspace's persisted variants.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from selfevals.arena.service import (
    CreateArenaRequest,
    RegisterVariantRequest,
    create_arena,
    gc_orphaned_worktrees,
    register_variant,
)
from selfevals.schemas.arena import ArenaVariant
from selfevals.schemas.enums import ArenaVariantState
from selfevals.schemas.workspace import Workspace
from selfevals.storage.factory import open_storage

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"

_AGENT_SCRIPT = """
import json, sys
sys.stdout.write(json.dumps({"content": "ok"}))
"""


def _run_git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init", "-q")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test")
    (repo / "agent.py").write_text(_AGENT_SCRIPT)
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-q", "-m", "initial")
    return repo


def _spec_template() -> dict[str, object]:
    return {
        "experiment": {
            "name": "placeholder",
            "goal": "g",
            "mode": "handoff",
            "taxonomy": {"target_features": ["general"], "dataset_types": ["capability"]},
            "datasets": {"optimization": {"id": "ds_x", "version": 1}},
            "target": {"primary": {"name": "pass@1", "operator": ">=", "value": 0.0}},
            "frozen": {
                "fleet": {"id": "flt_x"},
                "agents": [{"id": "ag_x"}],
                "datasets": [{"id": "ds_y"}],
            },
            "proposer": {"strategy": "manual", "parameters": {"proposals": [{}]}},
            "run": {"sandbox": "mock", "max_iterations": 1, "persist_traces": "none"},
        },
        "dataset": {
            "cases_inline": [
                {
                    "name": "t",
                    "task_type": "x",
                    "input": {"messages": [{"role": "user", "content": "hi"}]},
                    "taxonomy": {
                        "level": "final_response",
                        "feature": {"primary": "general"},
                        "source": {"type": "handcrafted"},
                        "ground_truth": {"methods": ["exact_match"]},
                        "dataset_type": "capability",
                    },
                    "expected": {"must_include": ["irrelevant"]},
                }
            ]
        },
        "graders": [{"type": "deterministic", "name": "rules"}],
    }


def _seed_workspace(db_url: str) -> None:
    storage = open_storage(db_url)
    try:
        with storage.open(WS) as scope:
            if not scope.exists(Workspace, WS):
                scope.put_entity(Workspace(id=WS, workspace_id=WS, slug="gc-t", name="gc-t"))
    finally:
        storage.close()


def _wait_ready(storage: object, variant_id: str, *, timeout: float = 10.0) -> None:
    import time

    deadline = time.monotonic() + timeout
    with storage.open(WS) as scope:  # type: ignore[attr-defined]
        while time.monotonic() < deadline:
            loaded = scope.get_entity(ArenaVariant, variant_id)
            assert isinstance(loaded, ArenaVariant)
            if loaded.state in (ArenaVariantState.READY, ArenaVariantState.FAILED):
                return
            time.sleep(0.1)
    raise AssertionError(f"variant {variant_id} did not settle")


def test_gc_is_noop_when_worktrees_root_missing(
    tmp_path: Path, db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SELFEVALS_WORKTREES_DIR", str(tmp_path / "does-not-exist"))
    storage = open_storage(db_url)
    try:
        assert gc_orphaned_worktrees(storage) == []
    finally:
        storage.close()


def test_gc_removes_directory_with_no_matching_variant(
    tmp_path: Path, db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktrees_dir = tmp_path / "worktrees"
    monkeypatch.setenv("SELFEVALS_WORKTREES_DIR", str(worktrees_dir))
    orphan_dir = worktrees_dir / "arn_ghost" / "var_ghost"
    orphan_dir.mkdir(parents=True)
    (orphan_dir / "marker.txt").write_text("leftover")

    _seed_workspace(db_url)
    storage = open_storage(db_url)
    try:
        removed = gc_orphaned_worktrees(storage)
        assert len(removed) == 1
        assert removed[0].path == str(orphan_dir.resolve())
        assert not orphan_dir.exists()
    finally:
        storage.close()


def test_gc_dry_run_lists_without_deleting(
    tmp_path: Path, db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktrees_dir = tmp_path / "worktrees"
    monkeypatch.setenv("SELFEVALS_WORKTREES_DIR", str(worktrees_dir))
    orphan_dir = worktrees_dir / "arn_ghost" / "var_ghost"
    orphan_dir.mkdir(parents=True)

    _seed_workspace(db_url)
    storage = open_storage(db_url)
    try:
        removed = gc_orphaned_worktrees(storage, dry_run=True)
        assert len(removed) == 1
        assert orphan_dir.exists()
    finally:
        storage.close()


def test_gc_preserves_worktree_owned_by_live_variant(
    tmp_path: Path, db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SELFEVALS_WORKTREES_DIR", str(tmp_path / "worktrees"))
    repo = _init_repo(tmp_path)
    default_branch = subprocess.run(
        ["git", "-C", str(repo), "branch", "--show-current"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    _seed_workspace(db_url)
    storage = open_storage(db_url)
    try:
        arena = create_arena(
            storage,
            workspace_id=WS,
            req=CreateArenaRequest(
                name="a",
                goal="g",
                repo_path=str(repo),
                agent_command=[sys.executable, "agent.py"],
                spec_template=_spec_template(),
                objective_metric="pass_rate",
            ),
        )
        variant = register_variant(
            storage,
            storage_url=db_url,
            workspace_id=WS,
            arena_id=arena.id,
            req=RegisterVariantRequest(name="main", git_ref=default_branch),
        )
        _wait_ready(storage, variant.id)

        with storage.open(WS) as scope:
            loaded = scope.get_entity(ArenaVariant, variant.id)
            assert isinstance(loaded, ArenaVariant)
            assert loaded.worktree_path is not None
            worktree_path = Path(loaded.worktree_path)
        assert worktree_path.exists()

        removed = gc_orphaned_worktrees(storage)
        assert removed == []
        assert worktree_path.exists()
    finally:
        storage.close()
