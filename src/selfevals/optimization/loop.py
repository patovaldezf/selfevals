"""OptimizationLoop: drive an Experiment through proposed iterations.

The loop:
1. Transitions the Experiment from DRAFT → QUEUED → RUNNING.
2. For each iteration, asks the Proposer for a Proposal.
3. Runs the proposal across the optimization dataset via the Executor,
   accumulating per-case GradeResults from the configured graders.
4. Aggregates results into IterationMetrics.
5. Hands the IterationAggregate to a DecisionEvaluator (PR 7) to compute
   a DecisionOutcome; persists an IterationRecord + DecisionRecord.
6. Terminates on convergence, max_iterations, exhausted search space,
   or unrecoverable error.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from selfevals._internal.ids import new_prefixed_id
from selfevals.analysis.staging import AnalysisStagingRecord
from selfevals.graders.base import GradeLabel, Grader, GraderContext, GradeResult
from selfevals.optimization.aggregator import (
    Aggregator,
    CaseOutcome,
    IterationAggregate,
    aggregate_iteration,
)
from selfevals.optimization.proposers import (
    Proposer,
    ProposerContext,
    SearchSpaceExhaustedError,
)
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
    from selfevals.runner.executor import CaseRun, Executor, RepetitionResult
    from selfevals.schemas.eval_case import EvalCase
    from selfevals.storage.interface import WorkspaceScope


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
    """Forward-declared shape for PR 7's evaluator. The default ignores
    everything and returns KEEP_CANDIDATE — the loop is decoupled from
    the matrix until PR 7 wires the real one in."""

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
        return DecisionOutcome.KEEP_CANDIDATE, "default evaluator (overridden in PR 7)"


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
    ) -> None:
        if not graders:
            raise ValueError("at least one grader is required")
        if not cases:
            raise ValueError("at least one case is required")
        if repetitions_per_case < 1:
            raise ValueError("repetitions_per_case must be >= 1")
        self._experiment = experiment
        self._executor = executor
        self._proposer = proposer
        self._graders = graders
        self._cases = cases
        self._scope = scope
        self._evaluator = decision_evaluator or _DefaultEvaluator()
        self._reps = repetitions_per_case

    @property
    def experiment(self) -> Experiment:
        return self._experiment

    def run(self) -> OptimizationResult:
        if self._experiment.state == ExperimentState.DRAFT:
            self._experiment.transition_to(ExperimentState.QUEUED)
        if self._experiment.state == ExperimentState.QUEUED:
            self._experiment.transition_to(ExperimentState.RUNNING)
        if self._experiment.state != ExperimentState.RUNNING:
            raise RuntimeError(
                f"OptimizationLoop.run() requires state in {{DRAFT, QUEUED, RUNNING}}; "
                f"got {self._experiment.state}"
            )

        result = OptimizationResult(experiment=self._experiment)
        baseline: IterationAggregate | None = None
        max_iter = self._experiment.run.max_iterations
        convergence = self._experiment.run.convergence
        recent_primary: list[float] = []
        # Dominant failure modes carried from the prior iteration — the context
        # the proposer is "shown" so a hypothesis can target a specific mode (§7).
        prev_failure_modes: list[str] = []

        for index in range(max_iter):
            context = ProposerContext(
                iteration_index=index,
                history=tuple(it.iteration_record for it in result.iterations),
            )
            try:
                proposal = self._proposer.propose(self._experiment, context)
            except SearchSpaceExhaustedError as exc:
                result.terminated_reason = f"search_space_exhausted: {exc}"
                break

            aggregate, case_runs, _per_case_grades = self._run_iteration(
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

            if _has_converged(recent_primary, convergence.min_delta, convergence.patience):
                result.terminated_reason = "converged"
                break
        else:
            result.terminated_reason = "max_iterations"

        # Finish state machine. ABORTED is reserved for explicit caller action.
        self._experiment.transition_to(ExperimentState.COMPLETED)
        return result

    def _run_iteration(
        self, proposal: Proposal, *, iteration: int
    ) -> tuple[IterationAggregate, list[CaseRun], dict[str, list[list[GradeResult]]]]:
        case_runs: list[CaseRun] = []
        per_case_grades: dict[str, list[list[GradeResult]]] = {}
        case_outcomes: list[CaseOutcome] = []
        for case in self._cases:
            case_run = self._executor.run_case(
                case,
                repetitions=self._reps,
                experiment_id=self._experiment.id,
                iteration=iteration,
                parameter_overrides=proposal.parameters,
            )
            case_runs.append(case_run)
            active_graders = _graders_for_case(self._graders, case)
            grades_per_rep: list[list[GradeResult]] = []
            for rep in case_run.repetitions:
                grades = [
                    g.grade(GraderContext(case=case, trace=rep.trace, response=rep.response))
                    for g in active_graders
                ]
                grades_per_rep.append(grades)
                self._maybe_persist_trace(rep, grades)
            per_case_grades[case.id] = grades_per_rep
            case_outcomes.append(Aggregator.case_outcome(case, case_run, grades_per_rep))
        primary_metric = self._experiment.target.primary.name
        reliability_metrics = self._experiment.reliability.metrics
        aggregate = aggregate_iteration(
            case_outcomes=case_outcomes,
            primary_metric=primary_metric,
            reliability_metrics=reliability_metrics,
        )
        return aggregate, case_runs, per_case_grades

    def _maybe_persist_trace(
        self, rep: RepetitionResult, grades: list[GradeResult]
    ) -> None:
        """Persist this repetition's trace per `run.persist_traces` (§5).

        The trace is stamped with its grader results first, so `analyze pull`
        can classify it without re-running the agent. `none` skips entirely;
        `failed` keeps only errored / failing-graded traces; `all` keeps them
        all. No-op without a scope (e.g. `--no-persist` runs).
        """
        mode = self._experiment.run.persist_traces
        if self._scope is None or mode == "none":
            return
        failed = rep.error is not None or any(
            g.label in (GradeLabel.FAIL, GradeLabel.ERROR, GradeLabel.PARTIAL) for g in grades
        )
        if mode == "failed" and not failed:
            return
        trace = rep.trace.model_copy(
            update={"grader_results": [_to_trace_grader_result(g) for g in grades]}
        )
        self._scope.put_entity(trace)

    def _build_iteration_record(
        self,
        *,
        iteration: int,
        proposal: Proposal,
        aggregate: IterationAggregate,
        case_runs: list[CaseRun],
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
            cost_usd=aggregate.total_cost_usd or None,
            duration_seconds=(
                aggregate.total_duration_ms / 1000 if aggregate.total_duration_ms else None
            ),
            failure_mode_counts=dict(aggregate.failure_mode_counts),
        )
        variant_id = new_prefixed_id("var")
        trace_run_ids = [rep.trace.run.run_id for run in case_runs for rep in run.repetitions]
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


def _to_trace_grader_result(grade: GradeResult) -> GraderResult:
    """Project the loop's `GradeResult` onto the trace's `GraderResult`.

    Carries the fields error analysis needs — grader, label, score, confidence,
    failure_modes. The free-text `reason` is dropped (the trace schema routes
    reasons through an object-store pointer, which the loop doesn't populate).
    """
    return GraderResult(
        grader=grade.grader,
        label=str(grade.label),
        score=grade.score,
        confidence=grade.confidence,
        failure_modes=list(grade.failure_modes),
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


def _has_converged(values: Iterable[float], min_delta: float, patience: int) -> bool:
    seq = list(values)
    if len(seq) < patience + 1:
        return False
    tail = seq[-patience - 1 :]
    best_old = max(tail[:-1])
    latest = tail[-1]
    return (latest - best_old) < min_delta
