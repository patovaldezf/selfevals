"""Thread assembly: load_thread groups traces sharing a thread_id into an
ordered conversation, each turn carrying its grader results."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bootstrap.api.queries import load_thread
from bootstrap.schemas.enums import SandboxMode, TraceState
from bootstrap.schemas.trace import (
    AgentSnapshotRef,
    EnvironmentInfo,
    FinalState,
    GraderResult,
    RunInfo,
    Trace,
)
from bootstrap.storage.seed import seed_workspace
from bootstrap.storage.sqlite import SQLiteStorage

T0 = datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC)


def _trace(
    workspace_id: str,
    *,
    thread_id: str | None,
    position: int | None,
    started_offset_s: int,
    grade: str | None = None,
) -> Trace:
    started = T0 + timedelta(seconds=started_offset_s)
    return Trace(
        id=Trace.make_id(),
        workspace_id=workspace_id,
        run=RunInfo(
            run_id=f"run_{started_offset_s}",
            experiment_id="exp_1",
            iteration=0,
            thread_id=thread_id,
            thread_position=position,
        ),
        agent=AgentSnapshotRef(agent_id="ag_x", agent_version=1),
        environment=EnvironmentInfo(
            framework_version="test",
            runtime="test",
            sandbox=SandboxMode.MOCK,
            started_at=started,
            ended_at=started + timedelta(seconds=1),
        ),
        final_state=FinalState(status=TraceState.COMPLETED),
        grader_results=(
            [GraderResult(grader="judge", label=grade, score=1.0)] if grade else []
        ),
    )


@pytest.fixture
def storage(tmp_path: Path) -> SQLiteStorage:
    st = SQLiteStorage(str(tmp_path / "bootstrap.sqlite"))
    seeded = seed_workspace(st, slug="t", name="t", user_id="local")
    ws = seeded.workspace
    # Three turns of one thread, inserted out of order, plus a decoy trace
    # in a different thread that must not leak in.
    with st.open(ws.id) as scope:
        scope.put_entity(_trace(ws.id, thread_id="th_1", position=2, started_offset_s=30, grade="pass"))
        scope.put_entity(_trace(ws.id, thread_id="th_1", position=0, started_offset_s=10, grade="pass"))
        scope.put_entity(_trace(ws.id, thread_id="th_1", position=1, started_offset_s=20, grade="fail"))
        scope.put_entity(_trace(ws.id, thread_id="th_other", position=0, started_offset_s=5))
    st._test_workspace_id = ws.id  # type: ignore[attr-defined]
    return st


def test_load_thread_orders_by_position_and_projects_grades(storage: SQLiteStorage) -> None:
    ws_id = storage._test_workspace_id  # type: ignore[attr-defined]
    thread = load_thread(storage, workspace_id=ws_id, thread_id="th_1")
    assert thread is not None
    assert thread.thread_id == "th_1"
    assert thread.turn_count == 3
    # Ordered by thread_position 0,1,2 — not insertion order.
    assert [t.position for t in thread.turns] == [0, 1, 2]
    assert [t.primary_grade for t in thread.turns] == ["pass", "fail", "pass"]
    # Grader results survive the projection.
    assert thread.turns[1].grader_results[0]["label"] == "fail"


def test_load_thread_falls_back_to_started_at_without_positions(tmp_path: Path) -> None:
    st = SQLiteStorage(str(tmp_path / "b.sqlite"))
    ws = seed_workspace(st, slug="t2", name="t2", user_id="local").workspace
    with st.open(ws.id) as scope:
        scope.put_entity(_trace(ws.id, thread_id="th_x", position=None, started_offset_s=30))
        scope.put_entity(_trace(ws.id, thread_id="th_x", position=None, started_offset_s=10))
        scope.put_entity(_trace(ws.id, thread_id="th_x", position=None, started_offset_s=20))
    thread = load_thread(st, workspace_id=ws.id, thread_id="th_x")
    assert thread is not None
    # No explicit positions → chronological by started_at; positions become 0,1,2.
    assert [t.run_id for t in thread.turns] == ["run_10", "run_20", "run_30"]
    assert [t.position for t in thread.turns] == [0, 1, 2]


def test_load_thread_unknown_returns_none(storage: SQLiteStorage) -> None:
    ws_id = storage._test_workspace_id  # type: ignore[attr-defined]
    assert load_thread(storage, workspace_id=ws_id, thread_id="nope") is None
