"""Thread assembly: load_thread groups traces sharing a thread_id into an
ordered conversation, each turn carrying its grader results."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from selfevals.api.queries import load_thread
from selfevals.schemas.enums import SandboxMode, TraceState
from selfevals.schemas.trace import (
    AgentSnapshotRef,
    EnvironmentInfo,
    FinalState,
    GraderResult,
    RunInfo,
    Trace,
)
from selfevals.storage.seed import seed_workspace
from selfevals.storage.sqlite import SQLiteStorage

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
    st = SQLiteStorage(str(tmp_path / "selfevals.sqlite"))
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
    # Each turn is a ScenarioResult: the primary grade is its `label`.
    assert [t.label for t in thread.turns] == ["pass", "fail", "pass"]
    assert thread.turns[1].matched is False
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


def _conversation_case(workspace_id: str) -> object:
    from selfevals.schemas.enums import (
        DatasetSource,
        DatasetType,
        GroundTruthMethod,
        Level,
    )
    from selfevals.schemas.eval_case import (
        CaseTaxonomy,
        EvalCase,
        Expected,
        FeatureTag,
        GroundTruthSpec,
        SourceInfo,
    )

    return EvalCase(
        id=EvalCase.make_id(),
        workspace_id=workspace_id,
        experiment_id="exp_1",
        name="multi-turn classify",
        task_type="classification",
        input={"messages": [{"role": "user", "content": "hi"}]},
        taxonomy=CaseTaxonomy(
            level=Level.FINAL_RESPONSE,
            feature=FeatureTag(primary="commerce.product_resolution"),
            source=SourceInfo(type=DatasetSource.HANDCRAFTED),
            ground_truth=GroundTruthSpec(methods=[GroundTruthMethod.EXACT_MATCH]),
            dataset_type=DatasetType.CAPABILITY,
        ),
        expected=Expected(structured_output={"intent": "greet"}),
    )


def _turn_trace(workspace_id: str, *, case_id: str, position: int, structured: dict) -> Trace:
    from selfevals.schemas.trace import TraceOutputs

    started = T0 + timedelta(seconds=position * 10)
    return Trace(
        id=Trace.make_id(),
        workspace_id=workspace_id,
        run=RunInfo(
            run_id=f"run_turn_{position}",
            experiment_id="exp_1",
            iteration=0,
            eval_case_id=case_id,
            thread_id="th_conv",
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
        outputs=TraceOutputs(structured_output=structured),
        grader_results=[GraderResult(grader="deterministic", label="pass", score=1.0)],
    )


def test_load_thread_turns_carry_expected_detected_per_turn(tmp_path: Path) -> None:
    """Each turn is a ScenarioResult with its own expected/detected derived from
    the turn's EvalCase — the fix for 'detected vs expected pendiente por turno'."""
    st = SQLiteStorage(str(tmp_path / "conv.sqlite"))
    ws = seed_workspace(st, slug="c", name="c", user_id="local").workspace
    case = _conversation_case(ws.id)
    with st.open(ws.id) as scope:
        scope.put_entity(case)
        scope.put_entity(_turn_trace(ws.id, case_id=case.id, position=0, structured={"intent": "greet"}))
        scope.put_entity(_turn_trace(ws.id, case_id=case.id, position=1, structured={"intent": "other"}))
    thread = load_thread(st, workspace_id=ws.id, thread_id="th_conv")
    assert thread is not None
    assert thread.turn_count == 2
    t0, t1 = thread.turns
    # Per-turn expected (from the case) vs detected (from each turn's trace).
    # load_thread returns ScenarioResult objects, so expected/detected are models.
    assert t0.expected is not None and t0.expected.structured_output == {"intent": "greet"}
    assert t0.detected is not None and t0.detected.structured_output == {"intent": "greet"}
    assert t1.detected is not None and t1.detected.structured_output == {"intent": "other"}
    # Same case for both turns.
    assert t0.case_id == t1.case_id == case.id
    st.close()
