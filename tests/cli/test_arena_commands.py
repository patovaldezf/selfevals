"""CLI tests for `selfevals arena` — the full create → variant add → round →
bundle → promote flow through `app()`, against a real throwaway git repo.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from selfevals.cli.main import app

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"

_AGENT_SCRIPT = """
import json, sys
req = json.loads(sys.stdin.read())
sys.stdout.write(json.dumps({"content": "pong"}))
"""


def _capture(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, str, str]:
    rc = app(argv)
    out = capsys.readouterr()
    return rc, out.out, out.err


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


def _create_arena(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], *, db: str, repo: Path
) -> str:
    spec_path = tmp_path / "spec_template.json"
    spec_path.write_text(json.dumps(_spec_template()))
    rc, out, err = _capture(
        capsys,
        [
            "--db", db, "arena", "create", WS,
            "--name", "ping-bakeoff",
            "--goal", "find the variant that says pong",
            "--repo", str(repo),
            "--command", sys.executable, "agent.py",
            "--objective-metric", "pass_rate",
            "--spec-json", f"@{spec_path}",
        ],
    )
    assert rc == 0, err
    return out.splitlines()[0].split("id=", 1)[1].strip()


def test_arena_create_persists_and_lists(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], db_url: str
) -> None:
    repo = _init_repo(tmp_path)
    arena_id = _create_arena(tmp_path, capsys, db=db_url, repo=repo)
    assert arena_id.startswith("arn_")

    rc, out, _ = _capture(capsys, ["--db", db_url, "arena", "list", WS])
    assert rc == 0
    assert arena_id in out
    assert "state=draft" in out


def test_arena_create_rejects_bad_spec_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], db_url: str
) -> None:
    repo = _init_repo(tmp_path)
    rc, _out, err = _capture(
        capsys,
        [
            "--db", db_url, "arena", "create", WS,
            "--name", "broken",
            "--goal", "g",
            "--repo", str(repo),
            "--command", "true",
            "--objective-metric", "pass_rate",
            "--spec-json", "{}",
        ],
    )
    assert rc == 2
    assert "error:" in err.lower()


def _wait_variant_ready(
    capsys: pytest.CaptureFixture[str], db: str, arena_id: str, *, timeout: float = 10.0
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rc, out, _ = _capture(capsys, ["--db", db, "arena", "show", WS, arena_id])
        assert rc == 0
        for line in out.splitlines():
            if "state=ready" in line:
                return
            if "state=failed" in line:
                raise AssertionError(f"variant failed to prepare:\n{out}")
        time.sleep(0.1)
    raise AssertionError("variant did not become ready in time")


def test_arena_full_flow_variant_round_bundle_promote(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    db_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SELFEVALS_WORKTREES_DIR", str(tmp_path / "worktrees"))
    repo = _init_repo(tmp_path)
    default_branch = subprocess.run(
        ["git", "-C", str(repo), "branch", "--show-current"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    arena_id = _create_arena(tmp_path, capsys, db=db_url, repo=repo)

    rc, out, err = _capture(
        capsys,
        [
            "--db", db_url, "arena", "variant", "add", WS, arena_id,
            "--name", "main", "--ref", default_branch, "--hypothesis", "baseline",
        ],
    )
    assert rc == 0, err
    variant_id = out.splitlines()[0].split("id=", 1)[1].split()[0].strip()
    assert variant_id.startswith("var_")

    _wait_variant_ready(capsys, db_url, arena_id)

    rc, out, err = _capture(capsys, ["--db", db_url, "arena", "round", WS, arena_id])
    assert rc == 0, err
    assert "launched round 0" in out

    deadline = time.monotonic() + 20.0
    completed = False
    while time.monotonic() < deadline:
        rc, out, _ = _capture(capsys, ["--db", db_url, "arena", "show", WS, arena_id])
        if "round=0  state=completed" in out:
            completed = True
            break
        time.sleep(0.2)
    assert completed, out

    rc, out, err = _capture(capsys, ["--db", db_url, "arena", "bundle", WS, arena_id])
    assert rc == 0, err
    bundle = json.loads(out)
    assert bundle["arena"]["id"] == arena_id
    assert len(bundle["variants"]) == 1
    assert bundle["leaderboard"][0]["variant_id"] == variant_id

    rc, out, err = _capture(
        capsys, ["--db", db_url, "arena", "promote", WS, arena_id, variant_id]
    )
    assert rc == 0, err
    assert "promoted variant" in out
    assert "suggested commands" in out

    rc, out, err = _capture(capsys, ["--db", db_url, "arena", "prune", WS, arena_id])
    assert rc == 0, err
    assert "state=archived" in out


def test_arena_variant_add_unknown_ref_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], db_url: str
) -> None:
    repo = _init_repo(tmp_path)
    arena_id = _create_arena(tmp_path, capsys, db=db_url, repo=repo)
    rc, _out, err = _capture(
        capsys,
        [
            "--db", db_url, "arena", "variant", "add", WS, arena_id,
            "--name", "ghost", "--ref", "no-such-branch",
        ],
    )
    assert rc == 2
    assert "error:" in err.lower()


def test_arena_show_unknown_arena_exits_2(
    capsys: pytest.CaptureFixture[str], db_url: str
) -> None:
    rc, _out, err = _capture(capsys, ["--db", db_url, "arena", "show", WS, "arn_doesnotexist"])
    assert rc == 2
    assert "not found" in err.lower()
