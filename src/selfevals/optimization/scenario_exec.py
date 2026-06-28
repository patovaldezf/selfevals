"""Execute + grade a single case — the shared unit of work.

Extracted from ``OptimizationLoop._run_one_case`` so the same exact path runs in
two places: the in-process loop (historically) and a sharded worker claiming one
scenario job. Keeping a single implementation is what guarantees a sharded run
grades identically to the golden — there is no second code path to drift.

The function takes its dependencies as explicit args (executor, graders, reps,
…) instead of reaching into loop ``self``, so a worker can build just an Executor
+ graders and call it. The conversation-collapse and grader-projection helpers
live here too, since they are part of the one case's lifecycle.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import TYPE_CHECKING

from selfevals.graders.base import (
    BreakdownNode,
    Grader,
    GraderContext,
    GradeResult,
)
from selfevals.runner.executor import CaseRun, RepetitionResult
from selfevals.schemas.trace import GraderResult as TraceGraderResult

if TYPE_CHECKING:
    from selfevals.runner.executor import Executor
    from selfevals.runner.multiturn import MultiTurnExecutor
    from selfevals.schemas.eval_case import EvalCase


async def execute_and_grade_case(
    case: EvalCase,
    *,
    executor: Executor,
    multi_turn_executor: MultiTurnExecutor,
    graders: list[Grader],
    repetitions: int,
    experiment_id: str,
    iteration: int,
    parameter_overrides: dict[str, object] | None,
    grade_concurrency: int,
) -> tuple[CaseRun, list[list[GradeResult]]]:
    """Run one case's repetitions, grade every (rep, grader) pair, stamp traces.

    Returns the CaseRun (with grader results stamped onto each rep's trace) and
    the ``grades_per_rep`` matrix (reps in order, grades in active-grader order).
    Identical to the loop's former ``_run_one_case`` body; the only change is
    that dependencies arrive as args.
    """
    sem = asyncio.Semaphore(grade_concurrency)

    async def _graded(grader: Grader, ctx: GraderContext) -> GradeResult:
        async with sem:
            return await grader.grade(ctx)

    runner = multi_turn_executor if case.is_conversation() else executor
    case_run = await runner.run_case(
        case,
        repetitions=repetitions,
        experiment_id=experiment_id,
        iteration=iteration,
        parameter_overrides=parameter_overrides,
    )
    active_graders = graders_for_case(graders, case)
    grade_tasks = [
        _graded(g, GraderContext(case=case, trace=rep.trace, response=rep.response))
        for rep in case_run.repetitions
        for g in active_graders
    ]
    flat_grades = await asyncio.gather(*grade_tasks)
    width = len(active_graders)
    grades_per_rep: list[list[GradeResult]] = [
        list(flat_grades[i * width : (i + 1) * width])
        for i in range(len(case_run.repetitions))
    ]
    stamped_reps = [
        replace(
            rep,
            trace=rep.trace.model_copy(
                update={"grader_results": [to_trace_grader_result(g) for g in grades]}
            ),
        )
        for rep, grades in zip(case_run.repetitions, grades_per_rep, strict=True)
    ]
    return replace(case_run, repetitions=stamped_reps), grades_per_rep


def graders_for_case(graders: list[Grader], case: EvalCase) -> list[Grader]:
    """Filter ``graders`` by ``case.graders`` if the case opts in.

    When the case lists no graders (default), every grader applies. When it lists
    names, only matching graders run — so one experiment can pair a deterministic
    grader with an LLM judge whose rubric fits only a subset of cases. If names
    are listed but none match, fall back to the full list (prior behaviour:
    everything runs) rather than silently producing zero grades.
    """
    if not case.graders:
        return graders
    wanted = set(case.graders)
    filtered = [g for g in graders if getattr(g, "name", None) in wanted]
    return filtered or graders


def to_trace_grader_result(grade: GradeResult) -> TraceGraderResult:
    """Project the loop's ``GradeResult`` onto the trace's ``GraderResult``.

    Carries grader/label/score/confidence/failure_modes plus the free-text
    ``reason`` (small text, inlined). The optional funnel ``breakdown`` is
    serialized to a plain dict so it persists alongside the result.
    """
    return TraceGraderResult(
        grader=grade.grader,
        label=str(grade.label),
        score=grade.score,
        reason=grade.reason,
        confidence=grade.confidence,
        failure_modes=list(grade.failure_modes),
        breakdown=grade.breakdown.to_dict() if grade.breakdown is not None else None,
    )


def collapse_conversation_turns(
    case_run: CaseRun,
    grades_per_turn: list[list[GradeResult]],
) -> tuple[CaseRun, list[list[GradeResult]]]:
    """Collapse per-turn results of a conversation case into per-thread ones.

    A conversation ``CaseRun`` holds one ``RepetitionResult`` per turn, with each
    trace's ``run.thread_id`` identifying its thread (= one logical repetition)
    and ``run.thread_position`` its turn index. This groups turns by thread and,
    per thread, produces a single synthetic repetition + grade: the final turn's
    trace is representative (output-state authoritative), and the synthetic grade
    takes the final turn's label/score with a ``conversation`` breakdown whose
    advisory (weight=0) ``turn_i`` children carry each turn's label/score for a
    funnel drill-down that does not affect metrics. Threads are ordered by first
    appearance for determinism.
    """
    order: list[str] = []
    by_thread: dict[str, list[tuple[RepetitionResult, list[GradeResult]]]] = {}
    for rep, grades in zip(case_run.repetitions, grades_per_turn, strict=True):
        thread_id = rep.trace.run.thread_id or rep.trace.id
        if thread_id not in by_thread:
            by_thread[thread_id] = []
            order.append(thread_id)
        by_thread[thread_id].append((rep, grades))

    collapsed_reps: list[RepetitionResult] = []
    collapsed_grades: list[list[GradeResult]] = []
    for rep_index, thread_id in enumerate(order):
        turns = sorted(
            by_thread[thread_id],
            key=lambda pair: pair[0].trace.run.thread_position or 0,
        )
        final_rep, final_grades = turns[-1]
        synthetic: list[GradeResult] = []
        for g_index, final_grade in enumerate(final_grades):
            children = []
            for position, (_, turn_grades) in enumerate(turns):
                turn_grade = turn_grades[g_index] if g_index < len(turn_grades) else None
                grader_children = (
                    list(turn_grade.breakdown.children)
                    if turn_grade is not None and turn_grade.breakdown is not None
                    else []
                )
                children.append(
                    BreakdownNode(
                        key=f"turn_{position}",
                        label=turn_grade.label if turn_grade is not None else None,
                        score=turn_grade.score if turn_grade is not None else None,
                        weight=0.0,
                        reason="per-turn diagnostic (advisory)",
                        children=grader_children,
                    )
                )
            synthetic.append(
                replace(
                    final_grade,
                    breakdown=BreakdownNode(
                        key="conversation",
                        label=final_grade.label,
                        score=final_grade.score,
                        children=children,
                    ),
                )
            )
        collapsed_reps.append(
            RepetitionResult(
                repetition=rep_index,
                trace=final_rep.trace,
                response=final_rep.response,
                error=next((r.error for r, _ in turns if r.error is not None), None),
            )
        )
        collapsed_grades.append(synthetic)

    return CaseRun(case_id=case_run.case_id, repetitions=collapsed_reps), collapsed_grades
