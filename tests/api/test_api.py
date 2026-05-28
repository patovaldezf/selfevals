"""Smoke tests for the HTTP bridge.

We pin against the real pingpong example so the test exercises the
same path the web UI will: `selfevals run` populates the SQLite db,
then the API reads back what's there.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from selfevals.api.app import build_app
from selfevals.cli.main import app as cli_app

# Starlette ships a deprecation warning we don't control; harmless for the API.
warnings.filterwarnings(
    "ignore",
    category=DeprecationWarning,
    module="starlette.formparsers",
)


REPO_EXAMPLE = (
    Path(__file__).resolve().parents[2] / "evals" / "experiments" / "example_pingpong.yaml"
)


@pytest.fixture
def seeded_db(tmp_path: Path) -> Path:
    db = tmp_path / "selfevals.sqlite"
    rc = cli_app(["--db", str(db), "run", str(REPO_EXAMPLE), "--max-iterations", "2"])
    assert rc == 0
    return db


@pytest.fixture
def client(seeded_db: Path) -> TestClient:
    return TestClient(build_app(db_path=str(seeded_db)))


def test_health_endpoint(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["db_path"].endswith("selfevals.sqlite")


def test_list_workspaces_returns_seeded(client: TestClient) -> None:
    response = client.get("/api/workspaces")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["workspaces"], list)
    assert len(body["workspaces"]) >= 1
    ws = body["workspaces"][0]
    for key in ("id", "slug", "name", "experiment_count"):
        assert key in ws


def test_workspace_show_404_for_unknown(client: TestClient) -> None:
    response = client.get("/api/workspaces/ws_does_not_exist")
    assert response.status_code == 404


def test_experiment_detail_and_iterations(client: TestClient) -> None:
    ws = client.get("/api/workspaces").json()["workspaces"][0]
    experiments = client.get(f"/api/workspaces/{ws['id']}/experiments").json()
    assert experiments, "expected at least one experiment to be seeded"
    exp = experiments[0]
    assert exp["primary_metric"] == "pass@1"

    detail = client.get(f"/api/workspaces/{ws['id']}/experiments/{exp['id']}").json()
    assert detail["summary"]["id"] == exp["id"]
    assert detail["result"] is not None
    assert detail["result"]["experiment"]["name"] == "pingpong baseline"
    # Iterations have monotonic indices and decision outcomes.
    assert len(detail["iterations"]) == 2
    decisions = {it["decision_outcome"] for it in detail["iterations"]}
    assert decisions <= {
        "keep_candidate",
        "reject",
        "revert",
        "investigate",
        "require_tradeoff_review",
        "spawn_subexperiment",
        "feature_flag",
        None,
    }


def test_decisions_endpoint_has_records(client: TestClient) -> None:
    ws = client.get("/api/workspaces").json()["workspaces"][0]
    experiments = client.get(f"/api/workspaces/{ws['id']}/experiments").json()
    exp = experiments[0]
    decisions = client.get(f"/api/workspaces/{ws['id']}/experiments/{exp['id']}/decisions").json()
    assert len(decisions) == 2
    for d in decisions:
        assert d["outcome"] in {
            "keep_candidate",
            "reject",
            "revert",
            "investigate",
            "require_tradeoff_review",
            "spawn_subexperiment",
            "feature_flag",
        }
        assert d["automated_rationale"]


def test_anchor_set_returns_points(client: TestClient) -> None:
    ws = client.get("/api/workspaces").json()["workspaces"][0]
    points = client.get(f"/api/workspaces/{ws['id']}/anchor-set").json()
    assert isinstance(points, list)
    # The pingpong example completes 2 iterations.
    assert len(points) == 2
    for p in points:
        assert p["primary_metric_name"] == "pass@1"


def test_trace_not_found_for_unknown_id(client: TestClient) -> None:
    ws = client.get("/api/workspaces").json()["workspaces"][0]
    response = client.get(f"/api/workspaces/{ws['id']}/traces/tr_missing")
    assert response.status_code == 404


def test_trace_response_exposes_experiment_name(client: TestClient) -> None:
    """A5: the trace viewer titles pages by experiment name, not by run_id.
    The API must surface the human name so the FE doesn't have to round-trip
    a second request for it."""
    ws = client.get("/api/workspaces").json()["workspaces"][0]
    experiments = client.get(f"/api/workspaces/{ws['id']}/experiments").json()
    exp = experiments[0]
    detail = client.get(f"/api/workspaces/{ws['id']}/experiments/{exp['id']}").json()
    run_ids = [
        rid
        for it in detail["iterations"]
        for rid in it["trace_run_ids"]
    ]
    assert run_ids, "expected at least one trace_run_id on the seeded iterations"
    trace = client.get(f"/api/workspaces/{ws['id']}/traces/{run_ids[0]}").json()
    assert trace["experiment_id"] == exp["id"]
    assert trace["experiment_name"] == exp["name"]


def test_resolve_payload_roundtrip(client: TestClient, seeded_db: Path) -> None:
    """Put bytes into the object store and resolve them via the API.

    The trace viewer needs this to lazy-load prompts/args/results that live
    behind `*_pointer` fields in span detail. This is the first endpoint
    that exposes the object store over HTTP.
    """
    from selfevals.storage.filesystem import FilesystemObjectStore

    ws = client.get("/api/workspaces").json()["workspaces"][0]
    store = FilesystemObjectStore(seeded_db.parent / "objects")
    payload = b'{"role": "user", "content": "ping"}'
    pointer = store.put(ws["id"], "messages", payload)

    response = client.get(
        f"/api/workspaces/{ws['id']}/payloads",
        params={"pointer": pointer},
    )
    assert response.status_code == 200
    assert response.content == payload
    # JSON-shaped content gets an application/json media type so the FE
    # can render it structurally without sniffing.
    assert response.headers["content-type"].startswith("application/json")


def test_resolve_payload_serves_plaintext_when_not_jsonish(
    client: TestClient, seeded_db: Path
) -> None:
    from selfevals.storage.filesystem import FilesystemObjectStore

    ws = client.get("/api/workspaces").json()["workspaces"][0]
    store = FilesystemObjectStore(seeded_db.parent / "objects")
    payload = b"You are a helpful assistant."
    pointer = store.put(ws["id"], "system_prompt", payload)

    response = client.get(
        f"/api/workspaces/{ws['id']}/payloads",
        params={"pointer": pointer},
    )
    assert response.status_code == 200
    assert response.content == payload
    assert response.headers["content-type"].startswith("text/plain")


def test_resolve_payload_rejects_cross_workspace_pointer(
    client: TestClient, seeded_db: Path
) -> None:
    """A pointer from workspace A served via workspace B's URL must 400,
    not silently leak the bytes. The pointer encodes its workspace, so the
    endpoint cross-checks against the URL.
    """
    from selfevals.storage.filesystem import FilesystemObjectStore

    ws = client.get("/api/workspaces").json()["workspaces"][0]
    store = FilesystemObjectStore(seeded_db.parent / "objects")
    pointer_in_ws = store.put(ws["id"], "x", b"secret")

    response = client.get(
        "/api/workspaces/ws_someone_else/payloads",
        params={"pointer": pointer_in_ws},
    )
    assert response.status_code == 400
    assert "workspace" in response.json()["detail"].lower()


def test_resolve_payload_rejects_malformed_pointer(client: TestClient) -> None:
    ws = client.get("/api/workspaces").json()["workspaces"][0]
    response = client.get(
        f"/api/workspaces/{ws['id']}/payloads",
        params={"pointer": "not-a-pointer"},
    )
    assert response.status_code == 400


def test_resolve_payload_404_when_missing(client: TestClient) -> None:
    """A correctly-formed pointer that doesn't exist on disk must 404."""
    ws = client.get("/api/workspaces").json()["workspaces"][0]
    # 64 hex zeros — valid shape, won't be on disk.
    fake = f"oss://{ws['id']}/sha256:{'0' * 64}"
    response = client.get(
        f"/api/workspaces/{ws['id']}/payloads",
        params={"pointer": fake},
    )
    assert response.status_code == 404


def test_span_summary_exposes_pointer_fields_to_fe() -> None:
    """Regression: `_span_summary` used to strip top-level `*_pointer` /
    `*_hash` fields from the view model, so the FE never saw them and
    couldn't ever resolve a prompt/args/result payload — even though the
    bytes were sitting in the object store. The trace viewer became
    debug theater. The projection must keep these fields so the
    PointerField widget can lazy-load them.
    """
    from datetime import UTC, datetime

    from selfevals.api.queries import _span_summary
    from selfevals.schemas.enums import SpanKind
    from selfevals.schemas.trace import LLMCallSpan, ToolCallSpan

    ws = "ws_test"
    fake_hash = "sha256:" + "0" * 64
    t = datetime(2026, 5, 28, 0, 0, 0, tzinfo=UTC)

    llm_span = LLMCallSpan(
        id="sp_llm",
        name="adapter_response",
        kind=SpanKind.LLM_CALL,
        started_at=t,
        provider="anthropic",
        model="claude-opus-4-7",
        system_prompt_pointer=f"oss://{ws}/{fake_hash}",
        system_prompt_hash=fake_hash,
        messages_pointer=f"oss://{ws}/{fake_hash}",
        messages_hash=fake_hash,
    )
    summary = _span_summary(llm_span)
    assert summary.detail.get("system_prompt_pointer") == f"oss://{ws}/{fake_hash}"
    assert summary.detail.get("system_prompt_hash") == fake_hash
    assert summary.detail.get("messages_pointer") == f"oss://{ws}/{fake_hash}"
    assert summary.detail.get("messages_hash") == fake_hash

    tool_span = ToolCallSpan(
        id="sp_tool",
        name="search",
        kind=SpanKind.TOOL_CALL,
        started_at=t,
        tool_name="search",
        args_pointer=f"oss://{ws}/{fake_hash}",
        args_hash=fake_hash,
        result_pointer=f"oss://{ws}/{fake_hash}",
        result_hash=fake_hash,
    )
    tsummary = _span_summary(tool_span)
    assert tsummary.detail.get("args_pointer") == f"oss://{ws}/{fake_hash}"
    assert tsummary.detail.get("args_hash") == fake_hash
    assert tsummary.detail.get("result_pointer") == f"oss://{ws}/{fake_hash}"
    assert tsummary.detail.get("result_hash") == fake_hash
