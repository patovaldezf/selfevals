from __future__ import annotations

from collections.abc import Iterator

import pytest

from selfevals.runner.launch import ensure_workspace_by_id
from selfevals.runner.pairwise_ops import (
    PairwiseOpError,
    compute_calibration,
    ingest_verdicts,
    list_verdicts,
)
from selfevals.schemas.pairwise_verdict import PairRef, PairwiseVerdict
from selfevals.storage.interface import StorageInterface, WorkspaceScope

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


@pytest.fixture
def scope(storage: StorageInterface) -> Iterator[WorkspaceScope]:
    """A workspace scope over a fresh per-test Postgres database.

    The workspace row must exist first — ``pairwise_verdicts.workspace_id`` has
    a real FK to ``workspaces``.
    """
    ensure_workspace_by_id(storage, WS)
    with storage.open(WS) as s:
        yield s


def _verdict(
    *,
    preferred: str = "a",
    margin: float = 0.5,
    judge_kind: str = "llm",
    judge_id: str = "llm:opus",
    rubric_version: int | None = 1,
    experiment_id: str | None = "exp_1",
    case_id: str | None = None,
    a_snapshot: str = "out-A",
    b_snapshot: str = "ref-B",
) -> PairwiseVerdict:
    return PairwiseVerdict(
        id=PairwiseVerdict.make_id(),
        workspace_id=WS,
        a_ref=PairRef(kind="agent_output", content_snapshot=a_snapshot),
        b_ref=PairRef(kind="reference", content_snapshot=b_snapshot),
        preferred=preferred,  # type: ignore[arg-type]
        margin=margin,
        judge_kind=judge_kind,  # type: ignore[arg-type]
        judge_id=judge_id,
        rubric_version=rubric_version,
        experiment_id=experiment_id,
        case_id=case_id,
    )


def test_ingest_and_list_round_trip(scope: WorkspaceScope) -> None:
    ingest_verdicts(scope, [_verdict(), _verdict(preferred="b")], validate_refs=False)
    got = list_verdicts(scope, experiment_id="exp_1")
    assert len(got) == 2
    assert {v.preferred for v in got} == {"a", "b"}


def test_list_filters_by_judge_kind(scope: WorkspaceScope) -> None:
    ingest_verdicts(
        scope,
        [_verdict(judge_kind="llm"), _verdict(judge_kind="human", judge_id="human:pato")],
        validate_refs=False,
    )
    humans = list_verdicts(scope, judge_kind="human")
    assert len(humans) == 1
    assert humans[0].judge_kind == "human"


def test_ingest_rejects_unknown_experiment_when_validating(scope: WorkspaceScope) -> None:
    with pytest.raises(PairwiseOpError, match="unknown experiment_id"):
        ingest_verdicts(scope, [_verdict(experiment_id="exp_missing")])
    # Nothing written: validation is fail-fast before any put.
    assert list_verdicts(scope) == []


def test_calibration_counts_agreement_on_same_pair(scope: WorkspaceScope) -> None:
    # Same pair (same snapshots), one LLM + one human verdict that AGREE.
    ingest_verdicts(
        scope,
        [
            _verdict(judge_kind="llm", preferred="a"),
            _verdict(judge_kind="human", judge_id="human:pato", preferred="a"),
        ],
        validate_refs=False,
    )
    report = compute_calibration(scope, experiment_id="exp_1")
    assert report.compared_pairs == 1
    assert report.agreements == 1
    assert report.disagreements == 0
    assert report.agreement_rate == pytest.approx(1.0)


def test_calibration_counts_disagreement_and_breaks_down_by_rubric(scope: WorkspaceScope) -> None:
    ingest_verdicts(
        scope,
        [
            # rubric v1: LLM says a, human says b -> disagree
            _verdict(judge_kind="llm", preferred="a", rubric_version=1, a_snapshot="p1"),
            _verdict(
                judge_kind="human", judge_id="human:p", preferred="b",
                rubric_version=1, a_snapshot="p1", margin=0.3,
            ),
            # rubric v2: both say a -> agree
            _verdict(judge_kind="llm", preferred="a", rubric_version=2, a_snapshot="p2"),
            _verdict(
                judge_kind="human", judge_id="human:p", preferred="a",
                rubric_version=2, a_snapshot="p2",
            ),
        ],
        validate_refs=False,
    )
    report = compute_calibration(scope, experiment_id="exp_1")
    assert report.compared_pairs == 2
    assert report.agreements == 1
    assert report.disagreements == 1
    by_version = {c.rubric_version: c for c in report.by_rubric_version}
    assert by_version[1].agreement_rate == pytest.approx(0.0)
    assert by_version[2].agreement_rate == pytest.approx(1.0)


def test_calibration_ignores_single_judge_pairs(scope: WorkspaceScope) -> None:
    # Only an LLM verdict on this pair — nothing to compare against.
    ingest_verdicts(scope, [_verdict(judge_kind="llm")], validate_refs=False)
    report = compute_calibration(scope, experiment_id="exp_1")
    assert report.compared_pairs == 0
