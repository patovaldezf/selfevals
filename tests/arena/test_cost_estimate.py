"""Tests for arena.service.estimate_round_cost and its enforcement of
budget.max_cost_usd in launch_round.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from selfevals.arena.service import (
    CreateArenaRequest,
    LaunchRoundRequest,
    RegisterVariantRequest,
    create_arena,
    estimate_round_cost,
    launch_round,
    register_variant,
)
from selfevals.schemas.arena import ArenaVariant
from selfevals.schemas.enums import ArenaVariantState
from selfevals.schemas.workspace import Workspace
from selfevals.storage.factory import open_storage

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"

_COSTED_AGENT = """
import json, sys
sys.stdout.write(json.dumps({"content": "pong", "cost_usd": 0.02}))
"""


def _run_git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init", "-q")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test")
    (repo / "agent.py").write_text(_COSTED_AGENT)
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
                    "input": {"messages": [{"role": "user", "content": "ping"}]},
                    "taxonomy": {
                        "level": "final_response",
                        "feature": {"primary": "general"},
                        "source": {"type": "handcrafted"},
                        "ground_truth": {"methods": ["exact_match"]},
                        "dataset_type": "capability",
                    },
                    "expected": {"must_include": ["pong"]},
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
                scope.put_entity(Workspace(id=WS, workspace_id=WS, slug="cost-t", name="cost-t"))
    finally:
        storage.close()


def _wait_ready(storage: object, variant_id: str, *, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    with storage.open(WS) as scope:  # type: ignore[attr-defined]
        while time.monotonic() < deadline:
            loaded = scope.get_entity(ArenaVariant, variant_id)
            assert isinstance(loaded, ArenaVariant)
            if loaded.state in (ArenaVariantState.READY, ArenaVariantState.FAILED):
                return
            time.sleep(0.1)
    raise AssertionError(f"variant {variant_id} did not settle")


def test_estimate_with_no_history_returns_none(tmp_path: Path, db_url: str) -> None:
    _seed_workspace(db_url)
    repo = _init_repo(tmp_path)
    storage = open_storage(db_url)
    try:
        arena = create_arena(
            storage,
            workspace_id=WS,
            req=CreateArenaRequest(
                name="fresh",
                goal="g",
                repo_path=str(repo),
                agent_command=[sys.executable, "agent.py"],
                spec_template=_spec_template(),
                objective_metric="pass_rate",
            ),
        )
        estimate = estimate_round_cost(
            storage, workspace_id=WS, arena_id=arena.id, variant_ids=["var_x"], reps=1
        )
        assert estimate.estimated_usd is None
        assert "no cost history" in estimate.basis
    finally:
        storage.close()


def test_estimate_extrapolates_from_completed_round(
    tmp_path: Path, db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SELFEVALS_WORKTREES_DIR", str(tmp_path / "worktrees"))
    _seed_workspace(db_url)
    repo = _init_repo(tmp_path)
    default_branch = subprocess.run(
        ["git", "-C", str(repo), "branch", "--show-current"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    storage = open_storage(db_url)
    try:
        arena = create_arena(
            storage,
            workspace_id=WS,
            req=CreateArenaRequest(
                name="costed",
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
        launch_round(
            storage, storage_url=db_url, workspace_id=WS, arena_id=arena.id, req=LaunchRoundRequest()
        )

        deadline = time.monotonic() + 20.0
        cost_seen = None
        while time.monotonic() < deadline:
            estimate = estimate_round_cost(
                storage, workspace_id=WS, arena_id=arena.id, variant_ids=[variant.id], reps=1
            )
            if estimate.estimated_usd is not None:
                cost_seen = estimate.estimated_usd
                break
            time.sleep(0.2)
        assert cost_seen == pytest.approx(0.02)

        # reps scales linearly.
        estimate_x3 = estimate_round_cost(
            storage, workspace_id=WS, arena_id=arena.id, variant_ids=[variant.id], reps=3
        )
        assert estimate_x3.estimated_usd == pytest.approx(0.06)

        # An unseen variant id falls back to the arena-wide average, not zero.
        estimate_unseen = estimate_round_cost(
            storage, workspace_id=WS, arena_id=arena.id, variant_ids=["var_never_run"], reps=1
        )
        assert estimate_unseen.estimated_usd == pytest.approx(0.02)
    finally:
        storage.close()


def test_launch_round_rejects_when_estimate_exceeds_budget(
    tmp_path: Path, db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SELFEVALS_WORKTREES_DIR", str(tmp_path / "worktrees"))
    _seed_workspace(db_url)
    repo = _init_repo(tmp_path)
    default_branch = subprocess.run(
        ["git", "-C", str(repo), "branch", "--show-current"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    storage = open_storage(db_url)
    try:
        arena = create_arena(
            storage,
            workspace_id=WS,
            req=CreateArenaRequest(
                name="tight-budget",
                goal="g",
                repo_path=str(repo),
                agent_command=[sys.executable, "agent.py"],
                spec_template=_spec_template(),
                objective_metric="pass_rate",
                budget={"max_cost_usd": 0.01},
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

        # First round: no history yet, so the estimate is None — budget can't
        # reject what it can't estimate. This launch is allowed through.
        launch_round(
            storage, storage_url=db_url, workspace_id=WS, arena_id=arena.id, req=LaunchRoundRequest()
        )

        deadline = time.monotonic() + 20.0
        has_cost = False
        while time.monotonic() < deadline:
            estimate = estimate_round_cost(
                storage, workspace_id=WS, arena_id=arena.id, variant_ids=[variant.id], reps=1
            )
            if estimate.estimated_usd is not None:
                has_cost = True
                break
            time.sleep(0.2)
        assert has_cost

        # Second round: now there IS history (~$0.02/round), which exceeds
        # the $0.01 budget — must be rejected before launching.
        with pytest.raises(Exception, match=r"exceeds budget\.max_cost_usd"):
            launch_round(
                storage,
                storage_url=db_url,
                workspace_id=WS,
                arena_id=arena.id,
                req=LaunchRoundRequest(),
            )
    finally:
        storage.close()
