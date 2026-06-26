"""OptimizationLoop: drive an Experiment through proposed iterations.

The loop:
0. Selects the optimization set from the supplied cases per
   `run.sample_strategy` / `SplitAllocation`, excluding `holdout=True` cases
   (spec §5, §11). Held-out cases are exposed via `holdout_cases` but never
   evaluated by the loop.
1. Transitions the Experiment from DRAFT → QUEUED → RUNNING.
2. For each iteration, asks the Proposer for a Proposal.
3. Runs the proposal across the optimization set via the Executor,
   accumulating per-case GradeResults from the configured graders.
4. Aggregates results into IterationMetrics.
5. Hands the IterationAggregate to a DecisionEvaluator to compute a
   DecisionOutcome; persists an IterationRecord + DecisionRecord.
6. Terminates on convergence, max_iterations, exhausted search space,
   or unrecoverable error.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from inspect import isawaitable
from typing import TYPE_CHECKING

from selfevals._internal.ids import new_prefixed_id
from selfevals.analysis.staging import AnalysisStagingRecord
from selfevals.graders.base import (
    BreakdownNode,
    GradeLabel,
    Grader,
    GraderContext,
    GradeResult,
)
from selfevals.optimization.aggregator import (
    Aggregator,
    CaseOutcome,
    IterationAggregate,
    aggregate_iteration,
)
from selfevals.optimization.proposers import (
    GridProposer,
    Proposer,
    ProposerContext,
    SearchSpaceExhaustedError,
)
from selfevals.optimization.sampling import select_optimization_set
from selfevals.runner.executor import CaseRun, RepetitionResult
from selfevals.runner.multiturn import MultiTurnExecutor
from selfevals.schemas.enums import DecisionOutcome, ExperimentState, IterationState
from selfevals.schemas.experiment import Experiment
from selfevals.schemas.iteration import (
    DecisionRationale,
    DecisionRecord,
    ExecutionInfo,
    IterationDecision,
    IterationMetrics,
    IterationRecord,
    MetricObservation,
    Proposal,
    ProposerInputs,
)
from selfevals.schemas.trace import GraderResult

if TYPE_CHECKING:
    from selfevals.analysis.hypothesis import HypothesisRecord
    from selfevals.runner.executor import Executor
    from selfevals.schemas.dataset import SplitAllocation
    from selfevals.schemas.eval_case import EvalCase
    from selfevals.storage.interface import WorkspaceScope


logger = logging.getLogger("selfevals.optimization")


@dataclass(frozen=True)
class IterationOutcome:
    iteration: int
    proposal: Proposal
    aggregate: IterationAggregate
    case_runs: list[CaseRun]
    iteration_record: IterationRecord
    decision_record: DecisionRecord


@dataclass
class OptimizationResult:
    experiment: Experiment
    iterations: list[IterationOutcome] = field(default_factory=list)
    terminated_reason: str = ""

    @property
    def best_iteration(self) -> IterationOutcome | None:
        if not self.iterations:
            return None
        return max(self.iterations, key=lambda it: it.aggregate.primary_value)


class DecisionEvaluatorProtocol:
    """Shape of a decision evaluator. The loop is decoupled from any
    concrete matrix; callers inject one (the CLI wires DecisionMatrixEvaluator),
    and the default below simply keeps every candidate."""

    def evaluate(
        self,
        *,
        experiment: Experiment,
        aggregate: IterationAggregate,
        baseline: IterationAggregate | None,
    ) -> tuple[DecisionOutcome, str]:
        raise NotImplementedError


class _DefaultEvaluator(DecisionEvaluatorProtocol):
    def evaluate(
        self,
        *,
        experiment: Experiment,
        aggregate: IterationAggregate,
        baseline: IterationAggregate | None,
    ) -> tuple[DecisionOutcome, str]:
        return DecisionOutcome.KEEP_CANDIDATE, "default evaluator (keep candidate)"


class OptimizationLoop:
    def __init__(
        self,
        *,
        experiment: Experiment,
        executor: Executor,
        proposer: Proposer,
        graders: list[Grader],
        cases: list[EvalCase],
        scope: WorkspaceScope | None = None,
        decision_evaluator: DecisionEvaluatorProtocol | None = None,
        repetitions_per_case: int = 1,
        grade_concurrency: int = 8,
        case_concurrency: int = 8,
        split_allocation: SplitAllocation | None = None,
    ) -> None:
        if not graders:
            raise ValueError("at least one grader is required")
        if not cases:
            raise ValueError("at least one case is required")
        if repetitions_per_case < 1:
            raise ValueError("repetitions_per_case must be >= 1")
        if grade_concurrency < 1:
            raise ValueError("grade_concurrency must be >= 1")
        if case_concurrency < 1:
            raise ValueError("case_concurrency must be >= 1")
        # Consume run.sample_strategy + holdout + SplitAllocation: the loop
        # evaluates only the optimization set; holdout=True cases are reserved
        # for a held-out gate and never touch the optimizer (spec §5, §11).
        split = select_optimization_set(cases, experiment.run, split_allocation=split_allocation)
        if not split.optimization:
            raise ValueError(
                "sampling produced an empty optimization set "
                f"(strategy={experiment.run.sample_strategy!r}, "
                f"{len(cases)} cases in, {len(split.holdout)} held out)"
            )
        self._experiment = experiment
        self._executor = executor
        # Conversation cases run turn-by-turn over the same executor; non-
        # conversation cases keep the single-shot path.
        self._multi_turn_executor = MultiTurnExecutor(executor)
        self._proposer = proposer
        self._graders = graders
        self._cases = split.optimization
        self._holdout_cases = split.holdout
        self._scope = scope
        self._evaluator = decision_evaluator or _DefaultEvaluator()
        self._reps = repetitions_per_case
        self._grade_concurrency = grade_concurrency
        self._case_concurrency = case_concurrency

    @property
    def experiment(self) -> Experiment:
        return self._experiment

    @property
    def optimization_cases(self) -> list[EvalCase]:
        """The cases the loop evaluates — the sampled, non-holdout set."""
        return list(self._cases)

    @property
    def holdout_cases(self) -> list[EvalCase]:
        """Cases held out of the optimization loop (spec §11). Returned so a
        caller can run them as a separate gate; the loop never evaluates them."""
        return list(self._holdout_cases)

    async def run(self) -> OptimizationResult:
        """Run the optimization loop, always closing the executor afterward.

        The `finally` stops the embedded OTLP receiver (if the executor started
        one) on every exit path — success, early-stop, or exception — so a
        long-lived `selfevals serve` doesn't leak receiver threads/ports across
        runs."""
        try:
            return await self._run_iterations()
        finally:
            self._executor.close()

    async def _run_iterations(self) -> OptimizationResult:
        if self._experiment.state == ExperimentState.DRAFT:
            self._experiment.transition_to(ExperimentState.QUEUED)
        if self._experiment.state == ExperimentState.QUEUED:
            self._experiment.transition_to(ExperimentState.RUNNING)
        if self._experiment.state != ExperimentState.RUNNING:
            raise RuntimeError(
                f"OptimizationLoop.run() requires state in {{DRAFT, QUEUED, RUNNING}}; "
                f"got {self._experiment.state}"
            )
        # Persist the RUNNING transition so a reader sees the experiment as
        # in-flight, not stuck at its pre-run state. The loop already writes
        # iterations/decisions/traces through `scope`; the experiment row was
        # the one thing that never got flushed (the CLI renders from the
        # in-memory result, so it never noticed — but a polling HTTP client
        # does). See the COMPLETED flush below for the terminal state.
        self._persist_experiment()

        result = OptimizationResult(experiment=self._experiment)
        baseline: IterationAggregate | None = None
        max_iter = self._experiment.run.max_iterations
        convergence = self._experiment.run.convergence
        # Whether a convergence plateau may stop the run early. The default is
        # proposer-aware: grid's contract is to enumerate the full cartesian
        # product, so a mid-grid plateau must NOT skip the rest (the "converged
        # after 4/6" trap — chunking x vector_weight combos went unprobed);
        # open-ended proposers (random/llm) do early-stop. `convergence.early_stop`
        # overrides either way. When early-stop is off, only the proposer's
        # SearchSpaceExhaustedError or max_iterations terminates the run.
        early_stop_enabled = _early_stop_enabled(convergence.early_stop, self._proposer)
        recent_primary: list[float] = []
        # Dominant failure modes carried from the prior iteration — the context
        # the proposer is "shown" so a hypothesis can target a specific mode (§7).
        prev_failure_modes: list[str] = []

        # Surface (don't hide) a grid that max_iterations would truncate: warn
        # and continue so the run still produces partial coverage (WARN +
        # CONTINUE — never abort).
        if isinstance(self._proposer, GridProposer):
            grid_size = self._proposer.grid_size(self._experiment)
            if max_iter < grid_size:
                logger.warning(
                    "grid has %d combinations but max_iterations=%d; %d combination(s) "
                    "will be skipped. Raise max_iterations to >= %d to cover the full grid.",
                    grid_size,
                    max_iter,
                    grid_size - max_iter,
                    grid_size,
                )

        for index in range(max_iter):
            pending = self._pending_hypotheses()
            context = ProposerContext(
                iteration_index=index,
                history=tuple(it.iteration_record for it in result.iterations),
                failure_modes=tuple(prev_failure_modes),
                pending_hypotheses=pending,
            )
            try:
                proposed = self._proposer.propose(self._experiment, context)
                proposal = await proposed if isawaitable(proposed) else proposed
            except SearchSpaceExhaustedError as exc:
                result.terminated_reason = f"search_space_exhausted: {exc}"
                break
            # A proposer may stamp `consumed_by_iteration` on a hypothesis it
            # applied (LLMProposer does). Persist those so they aren't re-offered.
            self._persist_consumed_hypotheses(pending)

            aggregate, case_runs, _per_case_grades, persisted_run_ids = await self._run_iteration(
                proposal, iteration=index
            )
            decision_outcome, rationale = self._evaluator.evaluate(
                experiment=self._experiment,
                aggregate=aggregate,
                baseline=baseline,
            )

            iteration_record = self._build_iteration_record(
                iteration=index,
                proposal=proposal,
                aggregate=aggregate,
                case_runs=case_runs,
                persisted_run_ids=persisted_run_ids,
                decision_outcome=decision_outcome,
                rationale=rationale,
                baseline=baseline,
                failure_modes_consulted=prev_failure_modes,
            )
            decision_record = DecisionRecord(
                id=DecisionRecord.make_id(),
                workspace_id=self._experiment.workspace_id,
                experiment_id=self._experiment.id,
                iteration=index,
                variant_id=iteration_record.execution.variant_id,
                outcome=decision_outcome,
                rationale=DecisionRationale(automated=rationale),
                metrics_snapshot={
                    aggregate.primary_metric: aggregate.primary_value,
                    **aggregate.guardrails,
                    **aggregate.reliability,
                },
            )
            if self._scope is not None:
                self._scope.put_entity(iteration_record)
                self._scope.put_entity(decision_record)

            result.iterations.append(
                IterationOutcome(
                    iteration=index,
                    proposal=proposal,
                    aggregate=aggregate,
                    case_runs=case_runs,
                    iteration_record=iteration_record,
                    decision_record=decision_record,
                )
            )

            self._maybe_stage_analysis(iteration=index, aggregate=aggregate)
            prev_failure_modes = _dominant_modes(aggregate.failure_mode_counts)

            recent_primary.append(aggregate.primary_value)
            if baseline is None or aggregate.primary_value > baseline.primary_value:
                baseline = aggregate

            if early_stop_enabled and _has_converged(
                recent_primary, convergence.min_delta, convergence.patience
            ):
                result.terminated_reason = "converged"
                break
        else:
            result.terminated_reason = "max_iterations"

        # Finish state machine. ABORTED is reserved for explicit caller action.
        self._experiment.transition_to(ExperimentState.COMPLETED)
        self._persist_experiment()
        return result

    def _persist_experiment(self) -> None:
        """Flush the experiment row to storage if a scope is attached.

        No-op for ephemeral runs (`scope is None`, e.g. CLI `--no-persist`).
        Idempotent w.r.t. the rest of the loop's writes — it touches only the
        experiment entity, whose version bumps on each put.
        """
        if self._scope is not None:
            self._scope.put_entity(self._experiment)

    async def _run_iteration(
        self, proposal: Proposal, *, iteration: int
    ) -> tuple[IterationAggregate, list[CaseRun], dict[str, list[list[GradeResult]]], list[str]]:
        case_runs: list[CaseRun] = []
        per_case_grades: dict[str, list[list[GradeResult]]] = {}
        case_outcomes: list[CaseOutcome] = []
        # run_ids of traces actually written to storage this iteration, in
        # case/rep order. Only these go onto the IterationRecord so the FE's
        # `/traces/{run_id}` never 404s on an announced-but-unstored trace.
        persisted_run_ids: list[str] = []
        sem = asyncio.Semaphore(self._grade_concurrency)

        async def _graded(grader: Grader, ctx: GraderContext) -> GradeResult:
            async with sem:
                return await grader.grade(ctx)

        # Fan out cases concurrently, bounded by the case-level semaphore. The
        # previous `for case in self._cases: await ...` ran cases strictly in
        # series — the dominant bottleneck at scale, since each case's adapter
        # call is the slow I/O. `_run_one_case` does only the awaitable work
        # (execute + grade + stamp); persistence and list assembly happen
        # afterwards in deterministic case order, so storage writes stay
        # single-threaded and `case_runs`/`persisted_run_ids` keep their order.
        case_sem = asyncio.Semaphore(self._case_concurrency)

        async def _run_one_case(case: EvalCase) -> tuple[CaseRun, list[list[GradeResult]]]:
            async with case_sem:
                # Conversation cases run turn-by-turn (one trace per turn, all
                # sharing a thread_id); everything else takes the single-shot path.
                runner = self._multi_turn_executor if case.is_conversation() else self._executor
                case_run = await runner.run_case(
                    case,
                    repetitions=self._reps,
                    experiment_id=self._experiment.id,
                    iteration=iteration,
                    parameter_overrides=proposal.parameters,
                )
                active_graders = _graders_for_case(self._graders, case)
                # Grade every (rep, grader) pair concurrently, bounded by the
                # grade semaphore. gather preserves order, so the flat result
                # splits back into grades_per_rep with reps in order and grades
                # in active_graders order.
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
                # Stamp the grader results onto each rep's trace so they ride
                # along in-memory on `case_runs` (the reporter and any consumer
                # of IterationOutcome can read `rep.trace.grader_results`
                # directly, including each grader's free-text reason).
                # RepetitionResult is frozen, so rebuild it; CaseRun is rebuilt.
                stamped_reps = [
                    replace(
                        rep,
                        trace=rep.trace.model_copy(
                            update={"grader_results": [_to_trace_grader_result(g) for g in grades]}
                        ),
                    )
                    for rep, grades in zip(case_run.repetitions, grades_per_rep, strict=True)
                ]
                return replace(case_run, repetitions=stamped_reps), grades_per_rep

        # gather preserves input order → `results[i]` corresponds to
        # `self._cases[i]`, keeping every downstream list in case order.
        results = await asyncio.gather(*(_run_one_case(case) for case in self._cases))

        for case, (case_run, grades_per_rep) in zip(self._cases, results, strict=True):
            case_runs.append(case_run)
            # Persist sequentially in case/rep order so storage writes stay
            # single-threaded and `persisted_run_ids` keeps its deterministic
            # order (the FE relies on it; see `_maybe_persist_trace`).
            for rep, grades in zip(case_run.repetitions, grades_per_rep, strict=True):
                persisted_id = self._maybe_persist_trace(rep, grades)
                if persisted_id is not None:
                    persisted_run_ids.append(persisted_id)
            per_case_grades[case.id] = grades_per_rep
            # A conversation case produced one trace per turn; collapse the
            # turns of each thread into a single per-thread outcome (final turn
            # authoritative, earlier turns advisory in a per-turn funnel) so the
            # aggregator counts threads as repetitions, not turns.
            if case.is_conversation():
                outcome_run, outcome_grades = _collapse_conversation_turns(case_run, grades_per_rep)
            else:
                outcome_run, outcome_grades = case_run, grades_per_rep
            case_outcomes.append(Aggregator.case_outcome(case, outcome_run, outcome_grades))
        primary_metric = self._experiment.target.primary.name
        reliability_metrics = self._experiment.reliability.metrics
        aggregate = aggregate_iteration(
            case_outcomes=case_outcomes,
            primary_metric=primary_metric,
            reliability_metrics=reliability_metrics,
            primary_grader=self._experiment.target.primary_grader,
        )
        return aggregate, case_runs, per_case_grades, persisted_run_ids

    def _maybe_persist_trace(
        self, rep: RepetitionResult, grades: list[GradeResult]
    ) -> str | None:
        """Persist this repetition's trace per `run.persist_traces` (§5).

        The rep's trace is already stamped with its grader results (see
        `_run_iteration`), so `analyze pull` can classify it without re-running
        the agent. `none` skips entirely; `failed` keeps only errored /
        failing-graded traces; `all` keeps them all. No-op without a scope
        (e.g. `--no-persist` runs).

        Returns the persisted trace's `run_id` when it actually wrote one,
        else None. The caller records only the persisted ids on the iteration's
        `trace_run_ids` so the FE never sees a `run_id` that resolves to 404
        (a trace that was announced but never stored — the prior bug).
        """
        mode = self._experiment.run.persist_traces
        if self._scope is None or mode == "none":
            return None
        failed = rep.error is not None or any(
            g.label in (GradeLabel.FAIL, GradeLabel.ERROR, GradeLabel.PARTIAL) for g in grades
        )
        if mode == "failed" and not failed:
            return None
        self._scope.put_entity(rep.trace)
        return rep.trace.run.run_id

    def _build_iteration_record(
        self,
        *,
        iteration: int,
        proposal: Proposal,
        aggregate: IterationAggregate,
        case_runs: list[CaseRun],
        persisted_run_ids: list[str],
        decision_outcome: DecisionOutcome,
        rationale: str,
        baseline: IterationAggregate | None,
        failure_modes_consulted: list[str],
    ) -> IterationRecord:
        primary_delta: float | None = None
        if baseline is not None:
            primary_delta = aggregate.primary_value - baseline.primary_value
        metrics = IterationMetrics(
            primary=MetricObservation(
                name=aggregate.primary_metric,
                value=aggregate.primary_value,
                delta_vs_baseline=primary_delta,
            ),
            guardrails=[
                MetricObservation(name=k, value=v) for k, v in aggregate.guardrails.items()
            ],
            reliability=dict(aggregate.reliability),
            error_rate=aggregate.error_rate,
            cost_usd=aggregate.total_cost_usd or None,
            duration_seconds=(
                aggregate.total_duration_ms / 1000 if aggregate.total_duration_ms else None
            ),
            failure_mode_counts=dict(aggregate.failure_mode_counts),
            funnel={key: node.to_dict() for key, node in aggregate.funnel.items()},
            confusion=(aggregate.confusion.to_dict() if aggregate.confusion is not None else None),
        )
        variant_id = new_prefixed_id("var")
        # Only the traces actually written to storage (see `_maybe_persist_trace`).
        # Announcing every rep's run_id — including the ones `persist_traces`
        # chose not to store — made `/traces/{run_id}` 404 on the unstored ones.
        trace_run_ids = list(persisted_run_ids)
        return IterationRecord(
            id=IterationRecord.make_id(),
            workspace_id=self._experiment.workspace_id,
            experiment_id=self._experiment.id,
            iteration=iteration,
            state=IterationState.COMPLETED,
            proposer=ProposerInputs(
                type=self._experiment.proposer.strategy,
                strategy_parameters=dict(self._experiment.proposer.parameters),
                iterations_consulted=list(range(iteration)),
                failure_modes_consulted=failure_modes_consulted,
            ),
            hypothesis=proposal.hypothesis,
            proposed_parameters=dict(proposal.parameters),
            execution=ExecutionInfo(
                variant_id=variant_id,
                ran_against={"case_count": len(case_runs)},
                trace_run_ids=trace_run_ids,
            ),
            metrics=metrics,
            decision=IterationDecision(
                outcome=decision_outcome,
                rationale=rationale,
            ),
        )

    def _maybe_stage_analysis(self, *, iteration: int, aggregate: IterationAggregate) -> None:
        """Persist an advisory staging marker when the trigger fires (§9).

        selfevals stages a bundle's worth of signal — it never runs an agent
        or an LLM. No-op when error analysis is disabled, when the run is
        healthy enough to stay under the threshold, or when there is nowhere
        to persist (the loop ran without a scope).
        """
        spec = self._experiment.error_analysis
        fail_rate = aggregate.fail_rate
        if self._scope is None or not spec.should_stage(fail_rate=fail_rate):
            return
        self._scope.put_entity(
            AnalysisStagingRecord(
                id=AnalysisStagingRecord.make_id(),
                workspace_id=self._experiment.workspace_id,
                experiment_id=self._experiment.id,
                iteration=iteration,
                fail_rate=fail_rate,
                threshold=spec.trigger.threshold,
                scope=spec.scope,
                reason=(
                    f"fail_rate {fail_rate:.0%} > threshold "
                    f"{spec.trigger.threshold:.0%}; run `selfevals analyze pull` "
                    f"{self._experiment.id} to code these failures"
                ),
            )
        )

    def _pending_hypotheses(self) -> tuple[HypothesisRecord, ...]:
        """HypothesisRecords for this experiment a proposer hasn't applied yet.

        Read fresh from storage each iteration and ordered oldest-first so an
        `LLMProposer` consumes them deterministically. Empty (no work) when the
        loop runs without a scope or analysis seeded none. See §7."""
        if self._scope is None:
            return ()
        from selfevals.analysis.hypothesis import HypothesisRecord

        records = [
            h
            for h in self._scope.list_entities(HypothesisRecord)
            if isinstance(h, HypothesisRecord)
            and h.experiment_id == self._experiment.id
            and h.consumed_by_iteration is None
        ]
        records.sort(key=lambda h: (h.created_at, h.id))
        return tuple(records)

    def _persist_consumed_hypotheses(self, offered: tuple[HypothesisRecord, ...]) -> None:
        """Write back any hypothesis a proposer stamped consumed this iteration."""
        if self._scope is None:
            return
        for hyp in offered:
            if hyp.consumed_by_iteration is not None:
                self._scope.put_entity(hyp)


def _collapse_conversation_turns(
    case_run: CaseRun,
    grades_per_turn: list[list[GradeResult]],
) -> tuple[CaseRun, list[list[GradeResult]]]:
    """Collapse per-turn results of a conversation case into per-thread ones.

    A conversation `CaseRun` holds one `RepetitionResult` per turn, with each
    trace's `run.thread_id` identifying its thread (= one logical repetition)
    and `run.thread_position` its turn index. This groups the turns by thread
    and, for each thread, produces a single synthetic repetition + grade:

    - the representative trace is the final turn's trace (output-state
      authoritative, matching "grade output-state, trajectory diagnostic");
    - the synthetic grade takes the final turn's label/score, and gets a
      `breakdown` rooted at `conversation` with one advisory (weight=0) child
      `turn_{i}` per turn carrying that turn's label/score, so the aggregator's
      funnel rollup surfaces a per-turn drill-down without affecting metrics.

    Threads are ordered by first appearance so results stay deterministic.
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
        # One synthetic grade per grader on the final turn, each carrying a
        # per-turn funnel breakdown.
        synthetic: list[GradeResult] = []
        for g_index, final_grade in enumerate(final_grades):
            children = []
            for position, (_, turn_grades) in enumerate(turns):
                turn_grade = turn_grades[g_index] if g_index < len(turn_grades) else None
                # Preserve the grader's own breakdown (e.g. the deterministic
                # per-rule funnel) under each turn, so a conversation case keeps
                # its rule-level drill-down instead of collapsing to a bare
                # pass/fail per turn. The grade's breakdown already roots at the
                # grader (`deterministic` → `must_include` → ...); we graft its
                # children directly under `turn_N` to avoid a redundant level.
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


def _to_trace_grader_result(grade: GradeResult) -> GraderResult:
    """Project the loop's `GradeResult` onto the trace's `GraderResult`.

    Carries the fields error analysis needs — grader, label, score, confidence,
    failure_modes — plus the free-text `reason`, which is inlined directly. A
    grader reason is small text, so it persists alongside the result; the
    trace's `reason_pointer` is reserved for large payloads the loop doesn't
    produce. The optional funnel `breakdown` is serialized to a plain dict so it
    persists alongside the result (additive; never changes the label/score).
    """
    return GraderResult(
        grader=grade.grader,
        label=str(grade.label),
        score=grade.score,
        reason=grade.reason,
        confidence=grade.confidence,
        failure_modes=list(grade.failure_modes),
        breakdown=grade.breakdown.to_dict() if grade.breakdown is not None else None,
    )


def _dominant_modes(failure_mode_counts: dict[str, int]) -> list[str]:
    """Mode identities ordered by frequency (desc), ties broken by id.

    This is the carryover the proposer is shown next iteration: the modes that
    hurt most, so a hypothesis can name one explicitly. Empty when no modes
    were tagged. See docs/spec/error_analysis_design.md §7.
    """
    return [mode for mode, _ in sorted(failure_mode_counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def _graders_for_case(graders: list[Grader], case: EvalCase) -> list[Grader]:
    """Filter `graders` by `case.graders` if the case opts in.

    When the case lists no graders (default), every grader applies — the
    pre-existing contract. When the case lists names, only matching
    graders run. This lets one experiment combine a deterministic grader
    with an LLM judge whose rubric is meaningful for only a subset of
    cases, without the judge contaminating unrelated cases.
    """
    if not case.graders:
        return graders
    wanted = set(case.graders)
    filtered = [g for g in graders if getattr(g, "name", None) in wanted]
    # If the case lists names but none match a registered grader, fall back
    # to the full list — this matches the prior behaviour (everything runs)
    # rather than silently producing zero grades.
    return filtered or graders


def _early_stop_enabled(override: bool | None, proposer: Proposer) -> bool:
    """Resolve whether a convergence plateau may stop the run early.

    An explicit `convergence.early_stop` wins. Otherwise the default is
    proposer-aware: the grid proposer enumerates a finite, fully-specified space
    and must visit all of it (no early-stop), while open-ended proposers
    (random / llm / manual) early-stop on a plateau as before.
    """
    if override is not None:
        return override
    return not isinstance(proposer, GridProposer)


def _has_converged(values: Iterable[float], min_delta: float, patience: int) -> bool:
    seq = list(values)
    if len(seq) < patience + 1:
        return False
    tail = seq[-patience - 1 :]
    best_old = max(tail[:-1])
    latest = tail[-1]
    return (latest - best_old) < min_delta
