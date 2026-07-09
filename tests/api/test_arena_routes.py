"""HTTP contract for the Arena endpoints: create/list/get/delete an arena,
register a variant, launch a round, and promote a winner — through
`TestClient` against real Postgres, with a real throwaway git repo backing
the agent under test.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient

from selfevals.api.app import build_app
from selfevals.schemas.workspace import Workspace
from selfevals.storage.factory import open_storage

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"

_AGENT_SCRIPT = """
import json, sys
req = json.loads(sys.stdin.read())
sys.stdout.write(json.dumps({"content": "ok"}))
"""


def _run(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(repo, "init", "-q")
    _run(repo, "config", "user.email", "test@example.com")
    _run(repo, "config", "user.name", "Test")
    (repo / "agent.py").write_text(_AGENT_SCRIPT)
    _run(repo, "add", ".")
    _run(repo, "commit", "-q", "-m", "initial")
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


@pytest.fixture
def client(db_url: str) -> Iterator[tuple[TestClient, str]]:
    storage = open_storage(db_url)
    try:
        with storage.open(WS) as scope:
            scope.put_entity(Workspace(id=WS, workspace_id=WS, slug="arena-http", name="arena-http"))
    finally:
        storage.close()
    yield TestClient(build_app(db_path=db_url)), db_url
    deadline = time.monotonic() + 20.0
    for thread in threading.enumerate():
        if thread.name.startswith(("run-", "arena-variant-")) and thread.is_alive():
            thread.join(timeout=max(0.0, deadline - time.monotonic()))


def _create_arena(c: TestClient, *, repo_path: str) -> dict[str, object]:
    res = c.post(
        f"/api/workspaces/{WS}/arenas",
        json={
            "name": "tts-bakeoff",
            "goal": "cheapest TTS with acceptable latency",
            "repo_path": repo_path,
            "agent_command": [sys.executable, "agent.py"],
            "spec_template": _spec_template(),
            "objective_metric": "pass_rate",
        },
    )
    assert res.status_code == 201, res.text
    return cast(dict[str, object], res.json())


def test_create_get_list_arena(client: tuple[TestClient, str], tmp_path: Path) -> None:
    c, _ = client
    repo = _init_repo(tmp_path)
    arena = _create_arena(c, repo_path=str(repo))
    assert arena["state"] == "draft"
    arena_id = arena["id"]

    got = c.get(f"/api/workspaces/{WS}/arenas/{arena_id}")
    assert got.status_code == 200
    assert got.json()["name"] == "tts-bakeoff"

    listed = c.get(f"/api/workspaces/{WS}/arenas")
    assert listed.status_code == 200
    assert any(a["id"] == arena_id for a in listed.json())


def test_create_arena_rejects_bad_spec_template(client: tuple[TestClient, str]) -> None:
    c, _ = client
    bad = _spec_template()
    del bad["dataset"]
    res = c.post(
        f"/api/workspaces/{WS}/arenas",
        json={
            "name": "broken",
            "goal": "g",
            "repo_path": "/tmp",
            "agent_command": ["true"],
            "spec_template": bad,
            "objective_metric": "pass_rate",
        },
    )
    assert res.status_code == 422


def test_get_unknown_arena_404(client: tuple[TestClient, str]) -> None:
    c, _ = client
    res = c.get(f"/api/workspaces/{WS}/arenas/arn_doesnotexist")
    assert res.status_code == 404


def test_git_refs_endpoint_lists_branches(
    client: tuple[TestClient, str], tmp_path: Path
) -> None:
    c, _ = client
    repo = _init_repo(tmp_path)
    _run(repo, "branch", "arena/variant-b")
    res = c.get(f"/api/workspaces/{WS}/git/refs", params={"repo_path": str(repo)})
    assert res.status_code == 200
    names = {r["name"] for r in res.json()["refs"]}
    assert "arena/variant-b" in names


def _wait_variant_ready(
    c: TestClient, arena_id: str, variant_id: str, *, timeout: float = 10.0
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    body: dict[str, object] = {}
    while time.monotonic() < deadline:
        res = c.get(f"/api/workspaces/{WS}/arenas/{arena_id}/variants")
        body = next(v for v in res.json() if v["id"] == variant_id)
        if body["state"] in ("ready", "failed"):
            return body
        time.sleep(0.1)
    raise AssertionError(f"variant {variant_id} did not settle: {body}")


def test_register_variant_and_launch_round_end_to_end(
    client: tuple[TestClient, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SELFEVALS_WORKTREES_DIR", str(tmp_path / "worktrees"))
    c, _ = client
    repo = _init_repo(tmp_path)
    default_branch = subprocess.run(
        ["git", "-C", str(repo), "branch", "--show-current"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    arena = _create_arena(c, repo_path=str(repo))
    arena_id = cast(str, arena["id"])

    variant_res = c.post(
        f"/api/workspaces/{WS}/arenas/{arena_id}/variants",
        json={"name": "main", "git_ref": default_branch},
    )
    assert variant_res.status_code == 202, variant_res.text
    variant = variant_res.json()
    assert variant["state"] == "preparing"

    ready = _wait_variant_ready(c, arena_id, cast(str, variant["id"]))
    assert ready["state"] == "ready"

    round_res = c.post(f"/api/workspaces/{WS}/arenas/{arena_id}/rounds", json={})
    assert round_res.status_code == 202, round_res.text
    round_body = round_res.json()
    assert round_body["state"] == "running"
    assert len(round_body["entries"]) == 1

    deadline = time.monotonic() + 20.0
    round_id = round_body["id"]
    final_round: dict[str, object] = round_body
    while time.monotonic() < deadline:
        got = c.get(f"/api/workspaces/{WS}/arenas/{arena_id}/rounds/{round_id}")
        final_round = got.json()
        if final_round["state"] == "completed":
            break
        time.sleep(0.2)
    assert final_round["state"] == "completed"

    # The list endpoint must reflect the same synced state as the detail
    # endpoint — regression coverage for a bug where GET .../rounds returned
    # the round frozen at its dispatch-time "running" snapshot forever.
    list_res = c.get(f"/api/workspaces/{WS}/arenas/{arena_id}/rounds")
    assert list_res.status_code == 200
    listed_round = next(r for r in list_res.json() if r["id"] == round_id)
    assert listed_round["state"] == "completed"

    bundle_res = c.get(f"/api/workspaces/{WS}/arenas/{arena_id}/bundle")
    assert bundle_res.status_code == 200, bundle_res.text
    bundle = bundle_res.json()
    assert bundle["arena"]["id"] == arena_id
    assert len(bundle["variants"]) == 1
    assert bundle["variants"][0]["variant_id"] == variant["id"]
    assert bundle["contract"]["register_variant"] == f"/api/workspaces/{WS}/arenas/{arena_id}/variants"

    promote_res = c.post(
        f"/api/workspaces/{WS}/arenas/{arena_id}/promote",
        json={"variant_id": variant["id"]},
    )
    assert promote_res.status_code == 200, promote_res.text
    promote_body = promote_res.json()
    assert promote_body["winner_variant_id"] == variant["id"]
    assert promote_body["suggested_commands"]


def test_register_variant_unknown_ref_returns_422(
    client: tuple[TestClient, str], tmp_path: Path
) -> None:
    c, _ = client
    repo = _init_repo(tmp_path)
    arena = _create_arena(c, repo_path=str(repo))
    res = c.post(
        f"/api/workspaces/{WS}/arenas/{arena['id']}/variants",
        json={"name": "ghost", "git_ref": "no-such-branch"},
    )
    assert res.status_code == 422


def test_delete_arena_archives_it(client: tuple[TestClient, str], tmp_path: Path) -> None:
    c, _ = client
    repo = _init_repo(tmp_path)
    arena = _create_arena(c, repo_path=str(repo))
    res = c.delete(f"/api/workspaces/{WS}/arenas/{arena['id']}")
    assert res.status_code == 204
    got = c.get(f"/api/workspaces/{WS}/arenas/{arena['id']}")
    assert got.json()["state"] == "archived"


def test_bundle_404_for_unknown_arena(client: tuple[TestClient, str]) -> None:
    c, _ = client
    res = c.get(f"/api/workspaces/{WS}/arenas/arn_doesnotexist/bundle")
    assert res.status_code == 404


def test_iterations_compare_any_experiment_rejects_unknown_ids(
    client: tuple[TestClient, str],
) -> None:
    c, _ = client
    res = c.get(
        f"/api/workspaces/{WS}/iterations/compare",
        params={"a": "itr_doesnotexist1", "b": "itr_doesnotexist2"},
    )
    assert res.status_code == 404
