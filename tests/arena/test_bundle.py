"""End-to-end tests for arena.bundle — the cross-variant contract an
external coding agent reads. Runs a full round through two real git-branch
variants (one echoing a correct-ish answer, one clearly worse) and asserts
the bundle's leaderboard/pairwise/exemplar-failures reflect real results.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from selfevals.arena.bundle import build_bundle
from selfevals.arena.service import (
    CreateArenaRequest,
    LaunchRoundRequest,
    RegisterVariantRequest,
    create_arena,
    launch_round,
    refresh_round_status,
    register_variant,
)
from selfevals.schemas.arena import ArenaVariant
from selfevals.schemas.enums import ArenaRoundState, ArenaVariantState
from selfevals.schemas.workspace import Workspace
from selfevals.storage.factory import open_storage

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"

# The grader checks must_include=["pong"]. The "good" variant answers "pong"
# (passes); the "bad" variant answers something else (fails) — this gives
# the bundle a real winner/loser pair with a genuine failure to surface.
_GOOD_AGENT = """
import json, sys
req = json.loads(sys.stdin.read())
sys.stdout.write(json.dumps({"content": "pong"}))
"""
_BAD_AGENT = """
import json, sys
req = json.loads(sys.stdin.read())
sys.stdout.write(json.dumps({"content": "nonsense"}))
"""


def _run(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(repo, "init", "-q")
    _run(repo, "config", "user.email", "test@example.com")
    _run(repo, "config", "user.name", "Test")
    (repo / "agent.py").write_text(_GOOD_AGENT)
    _run(repo, "add", ".")
    _run(repo, "commit", "-q", "-m", "good")
    return repo


def _spec_template() -> dict[str, object]:
    return {
        "experiment": {
            "name": "placeholder",
            "goal": "compare variants",
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
            "run": {"sandbox": "mock", "max_iterations": 1, "persist_traces": "all"},
        },
        "dataset": {
            "cases_inline": [
                {
                    "name": "t",
                    "task_type": "x",
                    "input": {"messages": [{"role": "user", "content": "ping"}]},
                    "taxonomy": {
                        "level": "final_response",
                        "feature": {"primary": "commerce.product_resolution"},
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
                scope.put_entity(Workspace(id=WS, workspace_id=WS, slug="bundle-t", name="bundle-t"))
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


def test_bundle_reflects_leaderboard_pairwise_and_exemplar_failures(
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
    _run(repo, "checkout", "-q", "-b", "variant-bad")
    (repo / "agent.py").write_text(_BAD_AGENT)
    _run(repo, "commit", "-q", "-am", "bad")
    _run(repo, "checkout", "-q", default_branch)

    _seed_workspace(db_url)
    storage = open_storage(db_url)
    try:
        arena = create_arena(
            storage,
            workspace_id=WS,
            req=CreateArenaRequest(
                name="ping-bakeoff",
                goal="find the variant that says pong",
                repo_path=str(repo),
                agent_command=[sys.executable, "agent.py"],
                spec_template=_spec_template(),
                objective_metric="pass_rate",
            ),
        )
        v_good = register_variant(
            storage,
            storage_url=db_url,
            workspace_id=WS,
            arena_id=arena.id,
            req=RegisterVariantRequest(name="good", git_ref=default_branch),
        )
        v_bad = register_variant(
            storage,
            storage_url=db_url,
            workspace_id=WS,
            arena_id=arena.id,
            req=RegisterVariantRequest(name="bad", git_ref="variant-bad"),
        )
        _wait_ready(storage, v_good.id)
        _wait_ready(storage, v_bad.id)

        round_ = launch_round(
            storage, storage_url=db_url, workspace_id=WS, arena_id=arena.id, req=LaunchRoundRequest()
        )
        deadline = time.monotonic() + 20.0
        refreshed = round_
        while time.monotonic() < deadline:
            refreshed = refresh_round_status(storage, workspace_id=WS, round_id=round_.id)
            if refreshed.state == ArenaRoundState.COMPLETED:
                break
            time.sleep(0.2)
        assert refreshed.state == ArenaRoundState.COMPLETED

        bundle = build_bundle(storage, workspace_id=WS, arena_id=arena.id)

        assert bundle.arena.id == arena.id
        assert bundle.arena.current_round == 1
        assert len(bundle.variants) == 2
        assert {c.variant_id for c in bundle.variants} == {v_good.id, v_bad.id}

        # Leaderboard: the "good" variant (answers "pong", passes the grader)
        # outranks the "bad" one.
        assert len(bundle.leaderboard) == 2
        assert bundle.leaderboard[0].variant_id == v_good.id
        assert bundle.leaderboard[0].rank == 1
        assert bundle.leaderboard[1].variant_id == v_bad.id
        assert bundle.leaderboard[1].delta_vs_best is not None
        assert bundle.leaderboard[1].delta_vs_best <= 0

        # Pairwise: best (good) vs the other (bad).
        assert len(bundle.pairwise_vs_best) == 1
        pw = bundle.pairwise_vs_best[0]
        assert pw.variant_id == v_bad.id
        assert pw.recommendation_kind in {"winner", "tie", "different_metric", "none"}

        # Exemplar failures: the bad variant should have at least one failed trace.
        bad_exemplars = next(e for e in bundle.exemplar_failures if e.variant_id == v_bad.id)
        assert len(bad_exemplars.traces) >= 1

        # Contract URLs are well-formed and reference this arena.
        assert bundle.contract.register_variant == f"/api/workspaces/{WS}/arenas/{arena.id}/variants"
        assert bundle.contract.launch_round == f"/api/workspaces/{WS}/arenas/{arena.id}/rounds"
        assert bundle.contract.promote == f"/api/workspaces/{WS}/arenas/{arena.id}/promote"

        # One round is nowhere near patience=3 — never claim convergence early.
        assert bundle.convergence.converged is False
        assert bundle.convergence.rounds_observed == 1
        assert bundle.convergence.best_value == 1.0
    finally:
        storage.close()


def test_bundle_for_arena_with_no_rounds_lists_variants_without_results(
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
                name="empty-arena",
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

        bundle = build_bundle(storage, workspace_id=WS, arena_id=arena.id)
        assert len(bundle.variants) == 1
        assert bundle.variants[0].latest_result is None
        assert bundle.leaderboard == []
        assert bundle.pairwise_vs_best == []
        assert bundle.round is None

        # No round has ever scored anything — no history to extrapolate a
        # plateau from, so convergence must read as "not converged," not "yes."
        assert bundle.convergence.converged is False
        assert bundle.convergence.rounds_observed == 0
        assert bundle.convergence.best_value is None
    finally:
        storage.close()


def test_convergence_signal_plateau_reads_as_converged() -> None:
    # Pure unit test of the plateau math, no storage: a flat best-per-round
    # series over enough rounds must read as converged.
    from selfevals.arena.bundle import _convergence_signal
    from selfevals.arena.schemas import RoundHistoryPoint, VariantCard

    card = VariantCard(
        variant_id="var_a",
        name="a",
        git_ref="main",
        state="ready",
        history=[RoundHistoryPoint(round=i, primary_value=0.8) for i in range(5)],
    )
    signal = _convergence_signal([card])
    assert signal.converged is True
    assert signal.rounds_observed == 5
    assert signal.best_value == 0.8


def test_convergence_signal_improving_series_is_not_converged() -> None:
    from selfevals.arena.bundle import _convergence_signal
    from selfevals.arena.schemas import RoundHistoryPoint, VariantCard

    card = VariantCard(
        variant_id="var_a",
        name="a",
        git_ref="main",
        state="ready",
        history=[RoundHistoryPoint(round=i, primary_value=0.1 * (i + 1)) for i in range(5)],
    )
    signal = _convergence_signal([card])
    assert signal.converged is False
    assert signal.best_value == pytest.approx(0.5)
