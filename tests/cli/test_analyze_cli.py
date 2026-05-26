"""End-to-end CLI for error analysis: init seeds taxonomy, pull emits a
bundle, push ingests a result, failuremode promote flips status."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bootstrap.cli.main import app
from bootstrap.schemas.enums import SandboxMode, TraceState
from bootstrap.schemas.trace import (
    AgentSnapshotRef,
    EnvironmentInfo,
    FinalState,
    GraderResult,
    RunInfo,
    Trace,
)
from bootstrap.storage.sqlite import SQLiteStorage

EXP = "exp_cli"


def _capture(capsys: pytest.CaptureFixture[str], argv: list[str], stdin: str = "") -> tuple[int, str]:
    if stdin:
        import io
        import sys

        sys.stdin = io.StringIO(stdin)  # type: ignore[assignment]
    try:
        rc = app(argv)
    finally:
        import sys

        sys.stdin = sys.__stdin__
    return rc, capsys.readouterr().out


def _seed_failed_trace(db: Path, ws: str) -> str:
    st = SQLiteStorage(str(db))
    trace = Trace(
        id=Trace.make_id(),
        workspace_id=ws,
        run=RunInfo(run_id="run_1", experiment_id=EXP, iteration=0),
        agent=AgentSnapshotRef(agent_id="ag", agent_version=1),
        environment=EnvironmentInfo(
            framework_version="t",
            runtime="t",
            sandbox=SandboxMode.MOCK,
            started_at=datetime(2026, 5, 25, tzinfo=UTC),
            ended_at=datetime(2026, 5, 25, tzinfo=UTC) + timedelta(seconds=1),
        ),
        final_state=FinalState(status=TraceState.COMPLETED),
        grader_results=[GraderResult(grader="judge", label="fail", score=0.0)],
    )
    with st.open(ws) as scope:
        scope.put_entity(trace)
    st.close()
    return trace.id


def test_full_analyze_cycle(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "b.sqlite"

    # init seeds the canonical taxonomy.
    rc, out = _capture(capsys, ["--db", str(db), "init", "w", "--name", "W"])
    assert rc == 0
    assert "canonical mode(s) seeded" in out
    ws = out.split("workspace id=")[1].split()[0]

    trace_id = _seed_failed_trace(db, ws)

    # pull emits a bundle with the failed trace and the seeded taxonomy.
    rc, out = _capture(capsys, ["--db", str(db), "analyze", "pull", ws, EXP])
    assert rc == 0
    bundle = json.loads(out)
    assert len(bundle["traces"]) == 1
    assert bundle["traces"][0]["trace_id"] == trace_id
    assert any(t["slug"] == "hallucinated" for t in bundle["taxonomy"])

    # push a result proposing a new candidate + assigning the trace to it.
    result = json.dumps(
        {
            "proposed_modes": [
                {"slug": "invented_price", "title": "Invented price", "definition": "…"}
            ],
            "assignments": [
                {"trace_id": trace_id, "new_mode_slug": "invented_price", "quote": "$499"}
            ],
        }
    )
    rc, out = _capture(
        capsys, ["--db", str(db), "analyze", "push", ws, EXP, "--by", "agent:test"], stdin=result
    )
    assert rc == 0
    assert "candidates created  : 1" in out

    # the candidate shows up and can be promoted.
    rc, out = _capture(capsys, ["--db", str(db), "failuremode", "list", ws, "--status", "candidate"])
    assert rc == 0
    assert "invented_price" in out
    fm_id = next(
        tok for line in out.splitlines() if "invented_price" in line
        for tok in line.split() if tok.startswith("fm_")
    )

    rc, out = _capture(capsys, ["--db", str(db), "failuremode", "promote", ws, fm_id])
    assert rc == 0
    assert "→ official" in out

    rc, out = _capture(capsys, ["--db", str(db), "failuremode", "list", ws, "--status", "official"])
    assert "invented_price" in out


def test_push_rejects_empty_stdin(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "b.sqlite"
    rc, out = _capture(capsys, ["--db", str(db), "init", "w", "--name", "W"])
    ws = out.split("workspace id=")[1].split()[0]
    rc, _ = _capture(capsys, ["--db", str(db), "analyze", "push", ws, EXP], stdin="   ")
    assert rc == 2  # BootstrapUserError → exit 2
