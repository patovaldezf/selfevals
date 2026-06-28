from __future__ import annotations

from selfevals.graders.base import BreakdownNode, GradeLabel, GradeResult
from selfevals.optimization.scenario_exec import to_trace_grader_result


def _sample_tree() -> BreakdownNode:
    return BreakdownNode(
        key="overall",
        label=GradeLabel.PARTIAL,
        score=0.6,
        weight=1.0,
        reason="mixed result",
        failure_modes=["root_mode"],
        children=[
            BreakdownNode(
                key="retrieval",
                label=GradeLabel.PASS,
                score=1.0,
                weight=2.0,
                reason="found docs",
            ),
            BreakdownNode(
                key="answer",
                label=GradeLabel.FAIL,
                score=0.2,
                weight=1.0,
                failure_modes=["hallucination"],
                children=[
                    BreakdownNode(key="citation", score=0.0, weight=0.5),
                ],
            ),
        ],
    )


def test_to_dict_is_json_serializable_and_recursive() -> None:
    import json

    node = _sample_tree()
    data = node.to_dict()
    # round-trips through json without custom encoders
    dumped = json.dumps(data)
    assert isinstance(dumped, str)
    assert data["key"] == "overall"
    assert data["label"] == "partial"
    assert data["children"][0]["key"] == "retrieval"
    assert data["children"][1]["children"][0]["key"] == "citation"


def test_from_dict_round_trips() -> None:
    node = _sample_tree()
    rebuilt = BreakdownNode.from_dict(node.to_dict())
    assert rebuilt == node


def test_label_only_node_round_trips_with_none_score() -> None:
    node = BreakdownNode(key="k", label=GradeLabel.PASS, score=None, weight=0.0)
    rebuilt = BreakdownNode.from_dict(node.to_dict())
    assert rebuilt.label == GradeLabel.PASS
    assert rebuilt.score is None
    assert rebuilt.weight == 0.0


def test_from_dict_defaults_for_minimal_payload() -> None:
    rebuilt = BreakdownNode.from_dict({"key": "bare"})
    assert rebuilt.key == "bare"
    assert rebuilt.label is None
    assert rebuilt.score is None
    assert rebuilt.weight == 1.0
    assert rebuilt.reason == ""
    assert rebuilt.failure_modes == []
    assert rebuilt.children == []


def test_grade_result_breakdown_defaults_to_none_and_is_additive() -> None:
    # A GradeResult without a breakdown keeps its authoritative label/score
    # and carries no funnel data.
    plain = GradeResult(grader="g", label=GradeLabel.PASS, reason="ok", score=1.0)
    assert plain.breakdown is None

    # Attaching a breakdown does not change the top-level verdict.
    with_breakdown = GradeResult(
        grader="g",
        label=GradeLabel.PASS,
        reason="ok",
        score=1.0,
        breakdown=BreakdownNode(key="overall", label=GradeLabel.FAIL, score=0.1),
    )
    assert with_breakdown.label == GradeLabel.PASS
    assert with_breakdown.score == 1.0
    # the breakdown can disagree with the verdict — it is informational only
    assert with_breakdown.breakdown is not None
    assert with_breakdown.breakdown.label == GradeLabel.FAIL


def test_conversion_to_persistible_preserves_breakdown_as_dict() -> None:
    grade = GradeResult(
        grader="g",
        label=GradeLabel.PARTIAL,
        reason="mixed",
        score=0.6,
        breakdown=_sample_tree(),
    )
    persisted = to_trace_grader_result(grade)
    assert persisted.breakdown is not None
    # round-trips back into a BreakdownNode equal to the original
    assert BreakdownNode.from_dict(persisted.breakdown) == _sample_tree()
    # top-level fields stay authoritative
    assert persisted.label == "partial"
    assert persisted.score == 0.6


def test_conversion_to_persistible_breakdown_none_when_absent() -> None:
    grade = GradeResult(grader="g", label=GradeLabel.PASS, reason="ok", score=1.0)
    persisted = to_trace_grader_result(grade)
    assert persisted.breakdown is None
