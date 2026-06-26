"""Error-analysis handshake: build_bundle (pull) + ingest_result (push).

Covers the contract invariants and the discover-once / classify-thereafter
stability property that is the whole point of the design.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from selfevals.analysis import build_bundle, ingest_result
from selfevals.analysis.ingest import AnalysisIngestError
from selfevals.analysis.schemas import (
    AnalysisResult,
    Assignment,
    Hypothesis,
    ProposedMode,
)
from selfevals.schemas.enums import FailureModeStatus, SandboxMode, TraceState
from selfevals.schemas.failure_mode import FailureMode
from selfevals.schemas.trace import (
    AgentSnapshotRef,
    EnvironmentInfo,
    FinalState,
    GraderResult,
    LLMCallSpan,
    RunInfo,
    Trace,
)
from selfevals.storage.factory import open_storage
from selfevals.storage.interface import StorageInterface
from selfevals.storage.seed import seed_workspace

T0 = datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC)
EXP = "exp_1"


def _failed_trace(ws: str, *, run_id: str, with_message: bool = True) -> Trace:
    spans = []
    if with_message:
        spans.append(
            LLMCallSpan(
                id="sp_1",
                name="model",
                started_at=T0,
                provider="anthropic",
                model="claude-sonnet-4-6",
                provider_metadata={
                    "selfevals.messages_in": [{"role": "user", "content": "what is X?"}],
                    "selfevals.messages_out": [{"role": "assistant", "content": "X costs $499"}],
                },
            )
        )
    return Trace(
        id=Trace.make_id(),
        workspace_id=ws,
        run=RunInfo(run_id=run_id, experiment_id=EXP, iteration=0),
        agent=AgentSnapshotRef(agent_id="ag", agent_version=1),
        environment=EnvironmentInfo(
            framework_version="t",
            runtime="t",
            sandbox=SandboxMode.MOCK,
            started_at=T0,
            ended_at=T0 + timedelta(seconds=1),
        ),
        final_state=FinalState(status=TraceState.COMPLETED),
        spans=spans,
        grader_results=[GraderResult(grader="judge", label="fail", score=0.0)],
    )


@pytest.fixture
def storage(db_url: str) -> Iterator[StorageInterface]:
    st = open_storage(db_url)
    seed_workspace(st, slug="w", name="w", user_id="local")
    try:
        yield st
    finally:
        st.close()


def _ws(st: StorageInterface) -> str:
    # Idempotent: seed_workspace returns the existing workspace seeded by the fixture.
    return seed_workspace(st, slug="w", name="w", user_id="local").workspace.id


def test_bundle_includes_failed_traces_with_transcript(storage: StorageInterface) -> None:
    ws = _ws(storage)
    with storage.open(ws) as scope:
        scope.put_entity(_failed_trace(ws, run_id="run_1"))
    bundle = build_bundle(storage, workspace_id=ws, experiment_id=EXP)
    assert len(bundle.traces) == 1
    bt = bundle.traces[0]
    assert bt.grade.label == "fail"
    assert [m.content for m in bt.transcript] == ["what is X?", "X costs $499"]


def test_bundle_excludes_passed_traces(storage: StorageInterface) -> None:
    ws = _ws(storage)
    passed = _failed_trace(ws, run_id="run_ok")
    passed.grader_results = [GraderResult(grader="judge", label="pass", score=1.0)]
    with storage.open(ws) as scope:
        scope.put_entity(passed)
    bundle = build_bundle(storage, workspace_id=ws, experiment_id=EXP)
    assert bundle.traces == []


def test_assignment_xor_enforced_on_wire_model() -> None:
    with pytest.raises(ValidationError):
        Assignment(trace_id="tr_1", mode_id="fm_x", new_mode_slug="y")  # both
    with pytest.raises(ValidationError):
        Assignment(trace_id="tr_1")  # neither


def test_ingest_rejects_unknown_mode_id(storage: StorageInterface) -> None:
    ws = _ws(storage)
    with storage.open(ws) as scope:
        scope.put_entity(_failed_trace(ws, run_id="run_1"))
    result = AnalysisResult(
        assignments=[Assignment(trace_id="tr_nope", mode_id="fm_does_not_exist")]
    )
    with pytest.raises(AnalysisIngestError):
        ingest_result(storage, workspace_id=ws, experiment_id=EXP, result=result)


def test_ingest_creates_candidate_and_stamps_trace(storage: StorageInterface) -> None:
    ws = _ws(storage)
    trace = _failed_trace(ws, run_id="run_1")
    with storage.open(ws) as scope:
        scope.put_entity(trace)
    result = AnalysisResult(
        proposed_modes=[
            ProposedMode(
                slug="invented_price",
                title="Invented price",
                definition="States a price not in the catalog.",
            )
        ],
        assignments=[
            Assignment(
                trace_id=trace.id,
                new_mode_slug="invented_price",
                open_note="quoted $499 with no catalog",
                quote="X costs $499",
            )
        ],
        hypotheses=[
            Hypothesis(
                targets_mode_slug="invented_price",
                statement="Add the catalog to the system prompt.",
            )
        ],
    )
    summary = ingest_result(
        storage, workspace_id=ws, experiment_id=EXP, result=result, proposed_by="agent:test"
    )
    assert len(summary.created_candidates) == 1
    assert summary.assignments_applied == 1
    assert summary.hypotheses_recorded == 1

    with storage.open(ws) as scope:
        modes = [m for m in scope.list_entities(FailureMode) if isinstance(m, FailureMode)]
        candidate = next(m for m in modes if m.slug == "invented_price")
        assert candidate.status == FailureModeStatus.CANDIDATE
        assert candidate.proposed_by == "agent:test"
        assert candidate.examples[0].trace_id == trace.id
        restamped = scope.get_entity(Trace, trace.id)
        assert isinstance(restamped, Trace)
        ea = next(g for g in restamped.grader_results if g.grader == "error_analysis")
        assert candidate.id in ea.failure_modes


def test_second_round_classifies_against_existing_no_duplicate(storage: StorageInterface) -> None:
    # The whole point: discover-once, classify-thereafter. Round 1 proposes a
    # mode; round 2 classifies a new trace against it by id — no second candidate.
    ws = _ws(storage)
    t1 = _failed_trace(ws, run_id="run_1")
    t2 = _failed_trace(ws, run_id="run_2")
    with storage.open(ws) as scope:
        scope.put_entity(t1)
        scope.put_entity(t2)

    round1 = AnalysisResult(
        proposed_modes=[
            ProposedMode(slug="invented_price", title="Invented price", definition="…")
        ],
        assignments=[Assignment(trace_id=t1.id, new_mode_slug="invented_price")],
    )
    s1 = ingest_result(storage, workspace_id=ws, experiment_id=EXP, result=round1)
    fm_id = s1.created_candidates[0]

    round2 = AnalysisResult(
        assignments=[Assignment(trace_id=t2.id, mode_id=fm_id)]
    )
    s2 = ingest_result(storage, workspace_id=ws, experiment_id=EXP, result=round2)
    assert s2.created_candidates == []
    assert s2.assignments_applied == 1

    with storage.open(ws) as scope:
        modes = [
            m
            for m in scope.list_entities(FailureMode)
            if isinstance(m, FailureMode) and m.slug == "invented_price"
        ]
        assert len(modes) == 1  # exactly one mode, two examples
        assert len(modes[0].examples) == 2


def test_reproposing_existing_slug_does_not_duplicate(storage: StorageInterface) -> None:
    ws = _ws(storage)
    t1 = _failed_trace(ws, run_id="run_1")
    with storage.open(ws) as scope:
        scope.put_entity(t1)
    r1 = AnalysisResult(
        proposed_modes=[ProposedMode(slug="dup", title="Dup", definition="d")],
        assignments=[Assignment(trace_id=t1.id, new_mode_slug="dup")],
    )
    ingest_result(storage, workspace_id=ws, experiment_id=EXP, result=r1)
    # Re-propose the same slug: should update, not create.
    r2 = AnalysisResult(
        proposed_modes=[ProposedMode(slug="dup", title="Dup edited", definition="d2")],
    )
    s2 = ingest_result(storage, workspace_id=ws, experiment_id=EXP, result=r2)
    assert s2.created_candidates == []
    assert len(s2.updated_candidates) == 1
    with storage.open(ws) as scope:
        dups = [
            m
            for m in scope.list_entities(FailureMode)
            if isinstance(m, FailureMode) and m.slug == "dup"
        ]
        assert len(dups) == 1
        # classify-don't-rename: the title/definition were NOT overwritten.
        assert dups[0].title == "Dup"
        assert dups[0].definition == "d"
