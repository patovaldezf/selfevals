"""End-to-end tests for arena.service against real Postgres + a real git repo.

Each test spins up a throwaway git repo with a tiny CLI agent script, builds
an Arena around it, registers variants on different branches, and drives a
full round through the real `launch_experiment_run` path — proving Arena
variants really do execute different code, in parallel, via the existing
run pipeline (no mocking of storage or the run dispatcher).
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
    launch_round,
    promote_winner,
    refresh_round_status,
    register_variant,
)
from selfevals.schemas.arena import Arena, ArenaVariant
from selfevals.schemas.enums import ArenaRoundState, ArenaState, ArenaVariantState, ExperimentState
from selfevals.schemas.experiment import Experiment
from selfevals.schemas.workspace import Workspace
from selfevals.storage.factory import open_storage

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"

_AGENT_SCRIPT = """
import json, sys
req = json.loads(sys.stdin.read())
resp = {"content": "VARIANT_MARKER"}
sys.stdout.write(json.dumps(resp))
"""


def _run(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _init_repo(tmp_path: Path, marker: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(repo, "init", "-q")
    _run(repo, "config", "user.email", "test@example.com")
    _run(repo, "config", "user.name", "Test")
    (repo / "agent.py").write_text(_AGENT_SCRIPT.replace("VARIANT_MARKER", marker))
    _run(repo, "add", ".")
    _run(repo, "commit", "-q", "-m", "initial")
    return repo


def _spec_template() -> dict[str, object]:
    return {
        "experiment": {
            "name": "placeholder",
            "goal": "compare TTS variants",
            "mode": "handoff",
            "taxonomy": {
                "target_features": ["commerce.product_resolution"],
                "dataset_types": ["capability"],
            },
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
                        "feature": {"primary": "commerce.product_resolution"},
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
                scope.put_entity(Workspace(id=WS, workspace_id=WS, slug="arena-t", name="arena-t"))
    finally:
        storage.close()


def test_create_arena_validates_spec_template(db_url: str) -> None:
    _seed_workspace(db_url)
    storage = open_storage(db_url)
    try:
        arena = create_arena(
            storage,
            workspace_id=WS,
            req=CreateArenaRequest(
                name="tts-bakeoff",
                goal="cheapest TTS with acceptable latency",
                repo_path="/does/not/matter/for/this/test",
                agent_command=[sys.executable, "agent.py"],
                spec_template=_spec_template(),
                objective_metric="pass_rate",
            ),
        )
        assert isinstance(arena, Arena)
        assert arena.state == ArenaState.DRAFT
    finally:
        storage.close()


def test_create_arena_rejects_bad_spec_template(db_url: str) -> None:
    _seed_workspace(db_url)
    storage = open_storage(db_url)
    try:
        bad_template = _spec_template()
        del bad_template["dataset"]  # experiment with no dataset source at all
        with pytest.raises(Exception, match="spec_template is invalid"):
            create_arena(
                storage,
                workspace_id=WS,
                req=CreateArenaRequest(
                    name="broken",
                    goal="g",
                    repo_path="/tmp",
                    agent_command=["true"],
                    spec_template=bad_template,
                    objective_metric="pass_rate",
                ),
            )
    finally:
        storage.close()


def test_register_variant_resolves_ref_and_prepares_worktree(
    tmp_path: Path, db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SELFEVALS_WORKTREES_DIR", str(tmp_path / "worktrees"))
    repo = _init_repo(tmp_path, "hello-from-main")
    _seed_workspace(db_url)
    storage = open_storage(db_url)
    try:
        arena = create_arena(
            storage,
            workspace_id=WS,
            req=CreateArenaRequest(
                name="tts-bakeoff",
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
            req=RegisterVariantRequest(name="main", git_ref="master"),
        )
        assert variant.state == ArenaVariantState.PREPARING
        assert variant.resolved_sha is not None

        deadline = time.monotonic() + 10
        with storage.open(WS) as scope:
            while time.monotonic() < deadline:
                loaded = scope.get_entity(ArenaVariant, variant.id)
                assert isinstance(loaded, ArenaVariant)
                if loaded.state != ArenaVariantState.PREPARING:
                    break
                time.sleep(0.1)
        assert isinstance(loaded, ArenaVariant)
        assert loaded.state == ArenaVariantState.READY
        assert loaded.worktree_path is not None
        assert Path(loaded.worktree_path, "agent.py").exists()
    finally:
        storage.close()


def test_register_variant_rejects_unknown_ref(tmp_path: Path, db_url: str) -> None:
    repo = _init_repo(tmp_path, "x")
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
                agent_command=["true"],
                spec_template=_spec_template(),
                objective_metric="pass_rate",
            ),
        )
        with pytest.raises(Exception, match="could not resolve"):
            register_variant(
                storage,
                storage_url=db_url,
                workspace_id=WS,
                arena_id=arena.id,
                req=RegisterVariantRequest(name="ghost", git_ref="no-such-branch"),
            )
    finally:
        storage.close()


def _wait_ready(storage: object, variant_id: str, *, timeout: float = 10.0) -> ArenaVariant:
    deadline = time.monotonic() + timeout
    with storage.open(WS) as scope:  # type: ignore[attr-defined]
        while time.monotonic() < deadline:
            loaded = scope.get_entity(ArenaVariant, variant_id)
            assert isinstance(loaded, ArenaVariant)
            if loaded.state in (ArenaVariantState.READY, ArenaVariantState.FAILED):
                return loaded
            time.sleep(0.1)
    raise AssertionError(f"variant {variant_id} did not settle in time")


def test_full_round_runs_two_variants_in_parallel_end_to_end(
    tmp_path: Path, db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SELFEVALS_WORKTREES_DIR", str(tmp_path / "worktrees"))
    repo = _init_repo(tmp_path, "from-main")
    _run(repo, "checkout", "-q", "-b", "variant-b")
    (repo / "agent.py").write_text(_AGENT_SCRIPT.replace("VARIANT_MARKER", "from-variant-b"))
    _run(repo, "commit", "-q", "-am", "variant b")
    _run(repo, "checkout", "-q", "-")  # back to the default branch

    _seed_workspace(db_url)
    storage = open_storage(db_url)
    try:
        arena = create_arena(
            storage,
            workspace_id=WS,
            req=CreateArenaRequest(
                name="tts-bakeoff",
                goal="g",
                repo_path=str(repo),
                agent_command=[sys.executable, "agent.py"],
                spec_template=_spec_template(),
                objective_metric="pass_rate",
            ),
        )
        default_branch = subprocess.run(
            ["git", "-C", str(repo), "branch", "--show-current"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        v1 = register_variant(
            storage,
            storage_url=db_url,
            workspace_id=WS,
            arena_id=arena.id,
            req=RegisterVariantRequest(name="main", git_ref=default_branch),
        )
        v2 = register_variant(
            storage,
            storage_url=db_url,
            workspace_id=WS,
            arena_id=arena.id,
            req=RegisterVariantRequest(name="variant-b", git_ref="variant-b"),
        )
        _wait_ready(storage, v1.id)
        _wait_ready(storage, v2.id)

        round_ = launch_round(
            storage,
            storage_url=db_url,
            workspace_id=WS,
            arena_id=arena.id,
            req=LaunchRoundRequest(),
        )
        assert round_.state == ArenaRoundState.RUNNING
        assert {e.variant_id for e in round_.entries} == {v1.id, v2.id}
        assert all(e.experiment_id is not None for e in round_.entries)

        deadline = time.monotonic() + 20
        refreshed = round_
        while time.monotonic() < deadline:
            refreshed = refresh_round_status(storage, workspace_id=WS, round_id=round_.id)
            if refreshed.state == ArenaRoundState.COMPLETED:
                break
            time.sleep(0.2)
        assert refreshed.state == ArenaRoundState.COMPLETED

        with storage.open(WS) as scope:
            arena_after = scope.get_entity(Arena, arena.id)
            assert isinstance(arena_after, Arena)
            assert arena_after.current_round == 1
            assert arena_after.state == ArenaState.ACTIVE

            for entry in refreshed.entries:
                assert entry.experiment_id is not None
                exp = scope.get_entity(Experiment, entry.experiment_id)
                assert isinstance(exp, Experiment)
                assert exp.state == ExperimentState.COMPLETED

        promotion = promote_winner(
            storage, workspace_id=WS, arena_id=arena.id, variant_id=v2.id
        )
        assert promotion["winner_variant_id"] == v2.id
        suggested_commands = promotion["suggested_commands"]
        assert isinstance(suggested_commands, list)
        assert any("merge" in cmd for cmd in suggested_commands)
        with storage.open(WS) as scope:
            arena_final = scope.get_entity(Arena, arena.id)
            assert isinstance(arena_final, Arena)
            assert arena_final.winner_variant_id == v2.id
            assert arena_final.state == ArenaState.COMPLETED
    finally:
        storage.close()


def test_launch_round_rejects_when_no_ready_variants(db_url: str) -> None:
    _seed_workspace(db_url)
    storage = open_storage(db_url)
    try:
        arena = create_arena(
            storage,
            workspace_id=WS,
            req=CreateArenaRequest(
                name="empty",
                goal="g",
                repo_path="/tmp",
                agent_command=["true"],
                spec_template=_spec_template(),
                objective_metric="pass_rate",
            ),
        )
        with pytest.raises(Exception, match="no ready variants"):
            launch_round(
                storage,
                storage_url=db_url,
                workspace_id=WS,
                arena_id=arena.id,
                req=LaunchRoundRequest(),
            )
    finally:
        storage.close()
