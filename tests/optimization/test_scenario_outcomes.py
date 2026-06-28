"""Lossless CaseOutcome <-> scenario_outcomes round-trip.

The sharded coordinator aggregates from persisted rows, not in-memory CaseRuns.
If rehydration drops or mistypes any field the sharded run's metrics would drift
from the in-process golden — so these tests pin that aggregating the originals
and aggregating the round-tripped copies yield an identical IterationAggregate,
both purely (to/from fields) and through real Postgres.
"""

from __future__ import annotations

from datetime import timedelta

from selfevals._internal.time import utc_now
from selfevals.graders.base import BreakdownNode, GradeLabel
from selfevals.optimization.aggregator import CaseOutcome, aggregate_iteration
from selfevals.optimization.scenario_outcomes import (
    aggregate_iteration_from_storage,
    case_outcome_from_fields,
    case_outcome_to_fields,
)
from selfevals.schemas.job import RunJob, ScenarioJob
from selfevals.schemas.workspace import Workspace
from selfevals.storage.factory import open_storage

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _outcomes() -> list[CaseOutcome]:
    """A spread that exercises every field: passes, fails, errors, partials,
    per-grader splits, nested breakdowns, failure weights, critical modes."""
    return [
        CaseOutcome(
            case_id="case_a",
            per_repetition_label=[GradeLabel.PASS, GradeLabel.FAIL, GradeLabel.PASS],
            per_repetition_score=[1.0, 0.0, 1.0],
            per_grader_label={
                "deterministic": [GradeLabel.PASS, GradeLabel.FAIL, GradeLabel.PASS],
                "judge": [GradeLabel.PASS, GradeLabel.PASS, GradeLabel.PASS],
            },
            failure_modes=["wrong_tool"],
            breakdowns=[
                BreakdownNode(
                    key="deterministic",
                    label=GradeLabel.FAIL,
                    score=0.0,
                    children=[BreakdownNode(key="must_include", label=GradeLabel.FAIL, weight=2.0)],
                )
            ],
            failure_weights={"wrong_tool": 3},
            critical_failure_modes=["data_loss"],
            cost_usd=0.0123,
            duration_ms=842,
            llm_call_count=4,
            cache_hit_count=1,
        ),
        CaseOutcome(
            case_id="case_b",
            per_repetition_label=[GradeLabel.ERROR],
            per_repetition_score=[0.0],
        ),
        CaseOutcome(
            case_id="case_c",
            per_repetition_label=[GradeLabel.PARTIAL, GradeLabel.PASS],
            per_repetition_score=[0.5, 1.0],
            failure_modes=["slow"],
        ),
    ]


def _aggregate(outcomes: list[CaseOutcome]) -> object:
    return aggregate_iteration(
        case_outcomes=outcomes,
        primary_metric="pass@1",
        reliability_metrics=["pass@1"],
        primary_grader="deterministic",
    )


def test_pure_round_trip_preserves_aggregate() -> None:
    originals = _outcomes()
    rehydrated = [case_outcome_from_fields(case_outcome_to_fields(o)) for o in originals]
    assert _aggregate(rehydrated) == _aggregate(originals)


def test_round_trip_through_postgres(db_url: str) -> None:
    originals = _outcomes()
    storage = open_storage(db_url)
    try:
        # Seed FKs: workspace, a minimal experiment row, the run_job.
        from tests.optimization.test_loop import _experiment

        exp = _experiment(max_iterations=1)
        exp_id = exp.id
        run_job = RunJob(
            id=RunJob.make_id(), workspace_id=WS, experiment_id=exp_id, spec_payload={}, reps=1
        )
        with storage.open(WS) as scope:
            scope.put_entity(Workspace(id=WS, workspace_id=WS, slug="t", name="t"))
            scope.put_entity(exp)
            scope.put_entity(run_job)

        now = utc_now()
        for o in originals:
            sj = ScenarioJob(
                id=ScenarioJob.make_id(), workspace_id=WS, run_job_id=run_job.id,
                experiment_id=exp_id, iteration=0, case_id=o.case_id,
            )
            with storage.open(WS) as scope:
                scope.put_entity(sj)
            storage.write_scenario_outcome(
                outcome_id="sco_" + o.case_id,
                workspace_id=WS,
                run_job_id=run_job.id,
                scenario_job_id=sj.id,
                experiment_id=exp_id,
                iteration=0,
                fields=case_outcome_to_fields(o),
                now=now + timedelta(seconds=1),
            )

        from_db = aggregate_iteration_from_storage(
            storage,
            run_job_id=run_job.id,
            iteration=0,
            primary_metric="pass@1",
            reliability_metrics=["pass@1"],
            primary_grader="deterministic",
        )
        assert from_db == _aggregate(originals)
    finally:
        storage.close()
