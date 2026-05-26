"""CLI command implementations.

Each `cmd_*` takes the parsed argparse Namespace and returns an int exit
code. Errors that should produce a clean `error: <msg>` line raise
`CommandError`; anything else escapes as a traceback (a real bug).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from bootstrap._errors import BootstrapUserError
from bootstrap.cli import _friendly
from bootstrap.decision.matrix import DecisionMatrixEvaluator
from bootstrap.graders.base import Grader
from bootstrap.graders.deterministic import DeterministicGrader
from bootstrap.graders.llm_judge import LLMJudgeGrader, RubricTemplate
from bootstrap.graders.registry import (
    available_graders,
    register_grader,
    resolve_graders,
    unregister_grader,
)
from bootstrap.optimization.aggregator import IterationAggregate
from bootstrap.optimization.loop import (
    IterationOutcome,
    OptimizationLoop,
    OptimizationResult,
)
from bootstrap.optimization.proposers import (
    GridProposer,
    ManualProposer,
    Proposer,
    RandomProposer,
)
from bootstrap.repo.loader import (
    AgentEntrypoint,
    ExperimentSpec,
    LoaderError,
    resolve_agent_callable,
)
from bootstrap.reporter import render_json, render_markdown
from bootstrap.reporter.compare import render_compare
from bootstrap.runner.adapters import (
    AdapterRequest,
    AdapterResponse,
    AgentAdapter,
    EmbeddedAdapter,
)
from bootstrap.runner.executor import Executor
from bootstrap.runner.sandbox import SandboxPolicy
from bootstrap.schemas.enums import ProposerStrategy
from bootstrap.schemas.experiment import Experiment
from bootstrap.schemas.iteration import DecisionRecord, IterationRecord
from bootstrap.schemas.workspace import Workspace
from bootstrap.storage.interface import ListFilter
from bootstrap.storage.seed import seed_failure_taxonomy, seed_workspace
from bootstrap.storage.sqlite import SQLiteStorage

if TYPE_CHECKING:
    from bootstrap.schemas._base import BaseEntity


class CommandError(BootstrapUserError):
    """Raised for user-correctable errors. CLI prints and exits 2.

    Thin alias of :class:`bootstrap._errors.BootstrapUserError` so the
    rest of this module keeps the historical name. Anything new outside
    the CLI should raise :class:`BootstrapUserError` directly.
    """


def _storage(args: argparse.Namespace) -> SQLiteStorage:
    """Open the SQLite db, translating sqlite errors into friendly messages.

    The two errors users actually hit (locked from a concurrent
    process, corrupted db pointed at by `--db`) both surface from the
    `sqlite3.connect` / first-PRAGMA path; the friendly layer turns
    them into a one-line CLI error.
    """
    import sqlite3

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        return SQLiteStorage(db_path)
    except sqlite3.Error as exc:
        raise _friendly.wrap_sqlite_error(exc, db_path=db_path) from exc


# --- init ---


def cmd_init(args: argparse.Namespace) -> int:
    storage = _storage(args)
    try:
        seeded = seed_workspace(
            storage,
            slug=args.slug,
            name=args.name or args.slug,
            user_id=args.user,
        )
        modes = seed_failure_taxonomy(storage, workspace_id=seeded.workspace.id)
    finally:
        storage.close()
    ws = seeded.workspace
    print(f"workspace id={ws.id} slug={ws.slug} name={ws.name}")
    print(f"members: {len(seeded.members)} role(s)")
    print(f"failure-mode taxonomy: {len(modes)} canonical mode(s) seeded")
    return 0


# --- workspace ---


def cmd_workspace_show(args: argparse.Namespace) -> int:
    storage = _storage(args)
    try:
        with storage.open(args.workspace_id) as scope:
            ws = _require_entity(scope, Workspace, args.workspace_id)
            assert isinstance(ws, Workspace)
            experiments = scope.list_entities(Experiment, ListFilter())
        print(f"workspace id={ws.id}")
        print(f"  slug:        {ws.slug}")
        print(f"  name:        {ws.name}")
        print(f"  owner:       {ws.owner_id}")
        print(f"  experiments: {len(experiments)}")
    finally:
        storage.close()
    return 0


# --- experiment ---


def cmd_experiment_list(args: argparse.Namespace) -> int:
    storage = _storage(args)
    try:
        with storage.open(args.workspace_id) as scope:
            experiments = scope.list_entities(Experiment, ListFilter())
    finally:
        storage.close()
    if not experiments:
        print("(no experiments)")
        return 0
    for exp in experiments:
        assert isinstance(exp, Experiment)
        print(f"{exp.id}  state={exp.state}  name={exp.name}")
    return 0


def cmd_experiment_show(args: argparse.Namespace) -> int:
    storage = _storage(args)
    try:
        with storage.open(args.workspace_id) as scope:
            exp = _require_entity(scope, Experiment, args.experiment_id)
            assert isinstance(exp, Experiment)
            iterations = _experiment_iterations(scope, exp.id)
        print(f"experiment id={exp.id}")
        print(f"  name:        {exp.name}")
        print(f"  goal:        {exp.goal}")
        print(f"  state:       {exp.state}")
        print(f"  mode:        {exp.mode}")
        print(f"  proposer:    {exp.proposer.strategy}")
        print(
            f"  target:      {exp.target.primary.name} "
            f"{exp.target.primary.operator} {exp.target.primary.value:g}"
        )
        print(f"  iterations:  {len(iterations)} of {exp.run.max_iterations}")
    finally:
        storage.close()
    return 0


# --- iteration ---


def cmd_iteration_list(args: argparse.Namespace) -> int:
    storage = _storage(args)
    try:
        with storage.open(args.workspace_id) as scope:
            iterations = _experiment_iterations(scope, args.experiment_id)
    finally:
        storage.close()
    if not iterations:
        print("(no iterations)")
        return 0
    for it in iterations:
        primary = it.metrics.primary if it.metrics else None
        primary_str = f"{primary.value:.4g}" if primary else "-"
        decision = it.decision.outcome if it.decision else "-"
        print(f"#{it.iteration:>3} {it.id}  {primary_str:>8}  {decision}")
    return 0


# --- report ---


def cmd_report(args: argparse.Namespace) -> int:
    storage = _storage(args)
    try:
        with storage.open(args.workspace_id) as scope:
            exp = _require_entity(scope, Experiment, args.experiment_id)
            assert isinstance(exp, Experiment)
            iterations = _experiment_iterations(scope, exp.id)
            decisions = _experiment_decisions(scope, exp.id)
    finally:
        storage.close()

    result = _reconstruct_result(exp, iterations, decisions)
    if args.format == "json":
        print(render_json(result))
    else:
        print(render_markdown(result))
    return 0


# --- compare ---


def cmd_compare(args: argparse.Namespace) -> int:
    storage = _storage(args)
    try:
        with storage.open(args.workspace_id) as scope:
            a = _require_entity(scope, IterationRecord, args.iter_a_id)
            b = _require_entity(scope, IterationRecord, args.iter_b_id)
    finally:
        storage.close()
    assert isinstance(a, IterationRecord)
    assert isinstance(b, IterationRecord)
    if a.experiment_id != b.experiment_id:
        raise CommandError(
            f"iterations belong to different experiments ({a.experiment_id} vs {b.experiment_id})"
        )
    if a.metrics is None or b.metrics is None:
        raise CommandError("one of the iterations has no metrics")

    print(render_compare(a, b))
    return 0


# --- estimate ---


def cmd_estimate(args: argparse.Namespace) -> int:
    if args.cases < 1 or args.space_size < 1 or args.reps < 1:
        raise CommandError("cases, space-size, and reps must all be >= 1")
    if args.cost_per_call < 0:
        raise CommandError("cost-per-call must be >= 0")
    total_calls = args.cases * args.reps * args.space_size
    total_cost = total_calls * args.cost_per_call
    print(f"cases x reps x proposals = {args.cases} x {args.reps} x {args.space_size}")
    print(f"agent calls (upper bound): {total_calls}")
    print(f"estimated cost (USD):      ${total_cost:.2f}")
    return 0


# --- internals ---


def _require_entity(scope: object, entity_type: type[BaseEntity], entity_id: str) -> BaseEntity:
    try:
        return scope.get_entity(entity_type, entity_id)  # type: ignore[attr-defined,no-any-return]
    except Exception as exc:
        raise CommandError(f"{entity_type.__name__} {entity_id} not found in workspace") from exc


def _experiment_iterations(scope: object, experiment_id: str) -> list[IterationRecord]:
    listed = scope.list_entities(IterationRecord, ListFilter())  # type: ignore[attr-defined]
    iterations = [
        it for it in listed if isinstance(it, IterationRecord) and it.experiment_id == experiment_id
    ]
    iterations.sort(key=lambda it: it.iteration)
    return iterations


def _experiment_decisions(scope: object, experiment_id: str) -> dict[int, DecisionRecord]:
    listed = scope.list_entities(DecisionRecord, ListFilter())  # type: ignore[attr-defined]
    by_iter: dict[int, DecisionRecord] = {}
    for d in listed:
        if not isinstance(d, DecisionRecord):
            continue
        if d.experiment_id != experiment_id:
            continue
        # Latest wins on duplicate (shouldn't happen in MVP).
        by_iter[d.iteration] = d
    return by_iter


def _reconstruct_result(
    experiment: Experiment,
    iterations: Sequence[IterationRecord],
    decisions: dict[int, DecisionRecord],
) -> OptimizationResult:
    """Build an OptimizationResult from persisted state.

    Some live-only fields (case_runs, per-case GradeResults) are lost
    once iterations hit disk — we surface what survives. The reporter
    only uses aggregate-level fields, so the report fidelity is intact.
    """
    from bootstrap.schemas.iteration import Proposal

    outcomes: list[IterationOutcome] = []
    for record in iterations:
        if record.metrics is None:
            continue
        primary = record.metrics.primary
        guardrails = {g.name: g.value for g in record.metrics.guardrails}
        reliability = dict(record.metrics.reliability)
        aggregate = IterationAggregate(
            primary_metric=primary.name,
            primary_value=primary.value,
            guardrails=guardrails,
            reliability=reliability,
            failure_mode_counts=dict(record.metrics.failure_mode_counts),
            total_cost_usd=record.metrics.cost_usd or 0.0,
            total_duration_ms=int((record.metrics.duration_seconds or 0.0) * 1000),
            case_count=int(record.execution.ran_against.get("case_count", 0)),
        )
        decision = decisions.get(record.iteration)
        if decision is None:
            # Defensive: skip orphans so the loop's invariants aren't violated.
            continue
        proposal = Proposal(
            parameters=dict(record.proposed_parameters),
            hypothesis=record.hypothesis,
        )
        outcomes.append(
            IterationOutcome(
                iteration=record.iteration,
                proposal=proposal,
                aggregate=aggregate,
                case_runs=[],
                iteration_record=record,
                decision_record=decision,
            )
        )
    return OptimizationResult(
        experiment=experiment,
        iterations=outcomes,
        terminated_reason="loaded_from_storage",
    )


# --- run ---


def cmd_run(args: argparse.Namespace) -> int:
    _ensure_cwd_on_path()
    spec = _friendly.load_spec(args.spec, workspace_id=args.workspace)

    if args.max_iterations is not None:
        if args.max_iterations < 1:
            raise CommandError("--max-iterations must be >= 1")
        spec.experiment.run.max_iterations = args.max_iterations

    try:
        callable_obj = resolve_agent_callable(spec.agent)
    except LoaderError as exc:
        raise CommandError(str(exc)) from exc
    adapter = _wrap_user_callable(callable_obj, spec.agent)
    proposer = _build_proposer(spec.experiment)
    registered_specs = _register_grader_specs(spec)
    try:
        graders = _resolve_case_graders(spec.cases)
    except Exception:
        for name in registered_specs:
            unregister_grader(name)
        raise

    storage = _storage(args) if not args.no_persist else None
    scope = None
    try:
        if storage is not None:
            _ensure_workspace(storage, spec)
            scope = storage.open(spec.workspace_id)
            scope.put_entity(spec.experiment)

        executor = Executor(
            adapter=adapter,
            sandbox=SandboxPolicy(spec.experiment.run.sandbox),
            workspace_id=spec.workspace_id,
        )
        loop = OptimizationLoop(
            experiment=spec.experiment,
            executor=executor,
            proposer=proposer,
            graders=graders,
            cases=spec.cases,
            scope=scope,
            decision_evaluator=DecisionMatrixEvaluator(),
            repetitions_per_case=args.reps,
        )
        result = loop.run()
    finally:
        if scope is not None:
            scope.close()
        if storage is not None:
            storage.close()
        for name in registered_specs:
            unregister_grader(name)

    if args.format == "json":
        print(render_json(result))
    else:
        print(render_markdown(result))
    return 0


def _ensure_cwd_on_path() -> None:
    """Make the user's project root importable when the CLI runs.

    `uv run bootstrap ...` invokes a console script whose `sys.path` does
    not include the cwd, so agent entrypoints like
    `examples.hello_llm.agent:run` would fail to import. We insert the
    cwd at the front of `sys.path` (once) so the resolver sees user
    packages.
    """
    cwd = str(Path.cwd())
    if cwd not in sys.path:
        sys.path.insert(0, cwd)


def _register_grader_specs(spec: ExperimentSpec) -> list[str]:
    """Register YAML-declared graders into the global registry.

    Returns the list of names that were registered so the caller can
    unregister them when the run ends — keeping the registry hermetic
    across consecutive invocations of `bootstrap run`.

    Deterministic specs override the default factory with one that uses
    the declared name. LLM-judge specs require a `judge_entrypoint` (or
    fall back to the agent entrypoint) so the rubric grader can invoke
    a real callable.
    """
    registered: list[str] = []
    for g_spec in spec.graders:
        if g_spec.type == "deterministic":
            register_grader(
                g_spec.name,
                _deterministic_factory(g_spec.name),
            )
            registered.append(g_spec.name)
            continue
        if g_spec.type == "llm_judge":
            entry = g_spec.judge_entrypoint or spec.agent
            try:
                judge_callable = resolve_agent_callable(entry)
            except LoaderError as exc:
                raise CommandError(str(exc)) from exc
            judge_adapter = _wrap_user_callable(judge_callable, entry)
            rubric = g_spec.rubric or ""
            register_grader(
                g_spec.name,
                _llm_judge_factory(g_spec.name, judge_adapter, rubric),
            )
            registered.append(g_spec.name)
            continue
        raise CommandError(f"unsupported grader type: {g_spec.type!r}")  # defensive
    return registered


def _deterministic_factory(name: str) -> Callable[[], Grader]:
    def _build() -> Grader:
        return DeterministicGrader(name=name)

    return _build


def _llm_judge_factory(name: str, judge_adapter: AgentAdapter, rubric: str) -> Callable[[], Grader]:
    template = RubricTemplate(rubric=rubric)

    def _build() -> Grader:
        return LLMJudgeGrader(name=name, judge_adapter=judge_adapter, rubric=template)

    return _build


def _resolve_case_graders(cases: Sequence[object]) -> list[Grader]:
    """Build the grader list the loop will run for every case.

    The default behaviour (no case declares a `graders:` list) is
    unchanged from the original `[DeterministicGrader()]`. As soon as a
    case names graders by string we route through the registry, which
    is the codepath that raises the "not registered" friendly error.
    """
    referenced: list[str] = []
    for case in cases:
        names = getattr(case, "graders", None) or []
        for n in names:
            if n not in referenced:
                referenced.append(n)
    if not referenced:
        return [DeterministicGrader()]
    # `resolve_graders` raises BootstrapUserError if any name is unknown,
    # listing the registry contents — the user-facing error path for (c).
    _ = available_graders()  # cheap, also guarantees registry import side effects.
    return list(resolve_graders(referenced))


# --- run helpers ---


def _wrap_user_callable(callable_obj: object, entrypoint: AgentEntrypoint) -> AgentAdapter:
    """Adapt the user's function into an AgentAdapter.

    Accepted return types from the user's callable:
    - AdapterResponse: passed through.
    - str: wrapped as `AdapterResponse(content=...)`.
    Anything else raises at invoke-time with a clear message — we don't
    silently coerce dicts or numbers because the grader semantics depend
    on a textual response.
    """
    if not callable(callable_obj):
        raise CommandError(
            f"agent entrypoint {entrypoint.raw!r} resolved to a non-callable "
            f"({type(callable_obj).__name__})"
        )

    def _adapt(req: AdapterRequest) -> AdapterResponse:
        result = callable_obj(req)
        if isinstance(result, AdapterResponse):
            return result
        if isinstance(result, str):
            return AdapterResponse(content=result)
        raise TypeError(
            f"agent entrypoint {entrypoint.raw!r} returned "
            f"{type(result).__name__}; expected str or AdapterResponse"
        )

    return EmbeddedAdapter(_adapt)


def _build_proposer(experiment: Experiment) -> Proposer:
    strategy = experiment.proposer.strategy
    if strategy == ProposerStrategy.GRID:
        return GridProposer()
    if strategy == ProposerStrategy.RANDOM:
        params = dict(experiment.proposer.parameters)
        return RandomProposer(
            max_proposals=int(params.get("max_proposals", 50)),
            seed=params.get("seed"),
        )
    if strategy == ProposerStrategy.MANUAL:
        manual = experiment.proposer.parameters.get("proposals")
        if not isinstance(manual, list) or not manual:
            raise CommandError(
                "manual proposer requires proposer.parameters.proposals as a non-empty list"
            )
        return ManualProposer(manual)
    raise CommandError(f"unsupported proposer strategy in MVP: {strategy}")


def _ensure_workspace(storage: SQLiteStorage, spec: ExperimentSpec) -> None:
    """Make sure the workspace row exists. Idempotent."""
    with storage.open(spec.workspace_id) as s:
        if s.exists(Workspace, spec.workspace_id):
            return
    ws = Workspace(
        id=spec.workspace_id,
        workspace_id=spec.workspace_id,
        slug=spec.workspace_id.lower(),
        name=spec.workspace_id,
    )
    with storage.open(spec.workspace_id) as s:
        s.put_entity(ws)
