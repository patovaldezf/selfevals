"""CLI command implementations.

Each `cmd_*` takes the parsed argparse Namespace and returns an int exit
code. Errors that should produce a clean `error: <msg>` line raise
`CommandError`; anything else escapes as a traceback (a real bug).
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import sys
from collections.abc import Callable, Sequence
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING

from selfevals._errors import SelfEvalsUserError
from selfevals.cli import _friendly
from selfevals.decision.matrix import DecisionMatrixEvaluator
from selfevals.graders.base import Grader
from selfevals.graders.deterministic import DeterministicGrader
from selfevals.graders.llm_judge import LLMJudgeGrader, RubricTemplate
from selfevals.graders.registry import (
    available_graders,
    register_grader,
    resolve_graders,
    unregister_grader,
)
from selfevals.optimization.aggregator import IterationAggregate
from selfevals.optimization.loop import (
    IterationOutcome,
    OptimizationLoop,
    OptimizationResult,
)
from selfevals.optimization.proposers import (
    GridProposer,
    LLMProposer,
    ManualProposer,
    Proposer,
    RandomProposer,
)
from selfevals.repo.loader import (
    AgentEntrypoint,
    AgentSpec,
    CliAgentSpec,
    EmbeddedAgentSpec,
    ExperimentSpec,
    HttpAgentSpec,
    LoaderError,
    resolve_agent_callable,
)
from selfevals.reporter import render_json, render_markdown
from selfevals.reporter.compare import render_compare
from selfevals.runner.adapters import (
    AdapterRequest,
    AdapterResponse,
    AgentAdapter,
    CliCommandAdapter,
    EmbeddedAdapter,
    HttpEndpointAdapter,
)
from selfevals.runner.executor import CaseRun, Executor, RepetitionResult
from selfevals.runner.sandbox import SandboxPolicy
from selfevals.schemas.enums import ProposerStrategy
from selfevals.schemas.experiment import Experiment
from selfevals.schemas.iteration import DecisionRecord, IterationRecord
from selfevals.schemas.trace import Trace
from selfevals.schemas.workspace import Workspace
from selfevals.storage.interface import ListFilter
from selfevals.storage.seed import seed_failure_taxonomy, seed_workspace
from selfevals.storage.sqlite import SQLiteStorage

if TYPE_CHECKING:
    from selfevals.schemas._base import BaseEntity


class CommandError(SelfEvalsUserError):
    """Raised for user-correctable errors. CLI prints and exits 2.

    Thin alias of :class:`selfevals._errors.SelfEvalsUserError` so the
    rest of this module keeps the historical name. Anything new outside
    the CLI should raise :class:`SelfEvalsUserError` directly.
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


def cmd_report(args: argparse.Namespace) -> int:
    storage = _storage(args)
    try:
        with storage.open(args.workspace_id) as scope:
            exp = _require_entity(scope, Experiment, args.experiment_id)
            assert isinstance(exp, Experiment)
            iterations = _experiment_iterations(scope, exp.id)
            decisions = _experiment_decisions(scope, exp.id)
            # Build the result while the scope is open — _reconstruct_result
            # reloads persisted Traces to repopulate case_runs / failure_reasons.
            result = _reconstruct_result(scope, exp, iterations, decisions)
    finally:
        storage.close()

    if args.format == "json":
        print(render_json(result))
    else:
        print(render_markdown(result))
    return 0


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


def cmd_skills_list(args: argparse.Namespace) -> int:
    from selfevals import skills

    names = skills.list_skills()
    if not names:
        print("(no bundled skills)")
        return 0
    for name in names:
        print(name)
    return 0


def cmd_skills_path(args: argparse.Namespace) -> int:
    from selfevals import skills

    try:
        path = skills.skill_path(args.name)
    except KeyError as exc:
        raise CommandError(str(exc)) from exc
    print(path)
    return 0


_EXAMPLE_NAMES = {"pingpong"}


def cmd_examples_copy(args: argparse.Namespace) -> int:
    name = args.name
    if name not in _EXAMPLE_NAMES:
        available = ", ".join(sorted(_EXAMPLE_NAMES))
        raise CommandError(f"unknown example {name!r}; available: {available}")

    target_root = Path(args.to)
    if target_root.exists() and not target_root.is_dir():
        raise CommandError(f"--to must be a directory: {target_root}")
    target_root.mkdir(parents=True, exist_ok=True)

    copied = _copy_example_tree(name=name, target_root=target_root)
    print(f"copied example {name!r} to {target_root}")
    for path in copied:
        print(f"  {path}")
    print("")
    print("Run:")
    print(
        f"  selfevals run {target_root / 'evals' / 'experiments' / 'example_pingpong.yaml'} --no-persist"
    )
    return 0


def _copy_example_tree(*, name: str, target_root: Path) -> list[Path]:
    source_root = resources.files("selfevals.examples").joinpath("evals")
    files = {
        source_root.joinpath("experiments", f"example_{name}.yaml"): target_root
        / "evals"
        / "experiments"
        / f"example_{name}.yaml",
        source_root.joinpath("datasets", f"{name}.jsonl"): target_root
        / "evals"
        / "datasets"
        / f"{name}.jsonl",
    }
    copied: list[Path] = []
    for source, dest in files.items():
        if not source.is_file():
            raise CommandError(f"packaged example file missing: {source}")
        if dest.exists():
            raise CommandError(f"refusing to overwrite existing file: {dest}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        copied.append(dest)
    return copied


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


def _load_case_runs(scope: object, experiment_id: str, iteration: int) -> list[CaseRun]:
    """Rehydrate an iteration's CaseRuns from persisted Trace entities.

    `run.persist_traces` writes each repetition's Trace (stamped with its
    grader_results) to storage; the IterationRecord only keeps the trace ids.
    Without re-reading the traces, a report rebuilt from disk would have empty
    `case_runs` and therefore empty `failure_reasons` — losing the per-grade
    rationales that an inline `run --format json` shows. Filter Trace entities
    by experiment+iteration (default `persist_traces=failed` keeps exactly the
    non-passing ones the reporter dedups), group by eval_case_id, and rebuild
    minimal CaseRuns. `response`/`error` stay None — the reporter only reads
    `trace.grader_results`.
    """
    listed = scope.list_entities(  # type: ignore[attr-defined]
        Trace,
        ListFilter(where={"run.experiment_id": experiment_id, "run.iteration": iteration}),
    )
    by_case: dict[str, list[RepetitionResult]] = {}
    for tr in listed:
        if not isinstance(tr, Trace):
            continue
        case_id = tr.run.eval_case_id or tr.run.run_id
        by_case.setdefault(case_id, []).append(
            RepetitionResult(repetition=tr.run.repetition, trace=tr, response=None, error=None)
        )
    case_runs: list[CaseRun] = []
    for case_id, reps in by_case.items():
        reps.sort(key=lambda r: r.repetition)
        case_runs.append(CaseRun(case_id=case_id, repetitions=reps))
    return case_runs


def _reconstruct_result(
    scope: object,
    experiment: Experiment,
    iterations: Sequence[IterationRecord],
    decisions: dict[int, DecisionRecord],
) -> OptimizationResult:
    """Build an OptimizationResult from persisted state.

    Aggregate-level fields come from the IterationRecord; `case_runs` are
    rehydrated from persisted Traces (see `_load_case_runs`) so the report's
    `failure_reasons` match an inline `run --format json`. Live-only fields the
    traces don't carry (e.g. the AdapterResponse) stay absent — the reporter
    doesn't read them.
    """
    from selfevals.schemas.iteration import Proposal

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
                case_runs=_load_case_runs(scope, experiment.id, record.iteration),
                iteration_record=record,
                decision_record=decision,
            )
        )
    return OptimizationResult(
        experiment=experiment,
        iterations=outcomes,
        terminated_reason="loaded_from_storage",
    )


def cmd_run(args: argparse.Namespace) -> int:
    _ensure_cwd_on_path()
    spec = _friendly.load_spec(args.spec, workspace_id=args.workspace)

    if args.max_iterations is not None:
        if args.max_iterations < 1:
            raise CommandError("--max-iterations must be >= 1")
        spec.experiment.run.max_iterations = args.max_iterations

    if args.persist_traces is not None:
        spec.experiment.run.persist_traces = args.persist_traces

    adapter = _build_adapter(spec.agent)
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
        result = asyncio.run(loop.run())
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

    `uv run selfevals ...` invokes a console script whose `sys.path` does
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
    across consecutive invocations of `selfevals run`.

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
            entry = g_spec.judge_entrypoint or _agent_entrypoint_for_judge(g_spec.name, spec.agent)
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
    # `resolve_graders` raises SelfEvalsUserError if any name is unknown,
    # listing the registry contents — the user-facing error path for (c).
    _ = available_graders()  # cheap, also guarantees registry import side effects.
    return list(resolve_graders(referenced))


def _build_adapter(agent: AgentSpec) -> AgentAdapter:
    """Dispatch the transport-tagged agent spec to a concrete adapter.

    This is the wiring point the loader defers to: importlib and adapter
    construction happen here, not in the (side-effect-free) loader.

    - embedded → resolve the callable and wrap it in `EmbeddedAdapter`.
    - cli      → `CliCommandAdapter(command, env, timeout_seconds)`.
    - http     → `HttpEndpointAdapter(url, headers, timeout_seconds)`.
    """
    if isinstance(agent, EmbeddedAgentSpec):
        try:
            callable_obj = resolve_agent_callable(agent.entrypoint)
        except LoaderError as exc:
            raise CommandError(str(exc)) from exc
        return _wrap_user_callable(callable_obj, agent.entrypoint)
    if isinstance(agent, CliAgentSpec):
        kwargs: dict[str, object] = {"env": agent.env}
        if agent.timeout_seconds is not None:
            kwargs["timeout_seconds"] = agent.timeout_seconds
        return CliCommandAdapter(agent.command, **kwargs)  # type: ignore[arg-type]
    if isinstance(agent, HttpAgentSpec):
        http_kwargs: dict[str, object] = {"headers": agent.headers}
        if agent.timeout_seconds is not None:
            http_kwargs["timeout_seconds"] = agent.timeout_seconds
        return HttpEndpointAdapter(agent.url, **http_kwargs)  # type: ignore[arg-type]
    raise CommandError(f"unsupported agent spec: {type(agent).__name__}")  # defensive


def _agent_entrypoint_for_judge(grader_name: str, agent: AgentSpec) -> AgentEntrypoint:
    """Resolve the judge fallback to the agent's entrypoint.

    The `judge_entrypoint`-omitted fallback only makes sense for an
    embedded agent — a cli/http agent has no in-process callable to reuse
    as a judge. Surface a friendly error pointing the user at the fix.
    """
    if isinstance(agent, EmbeddedAgentSpec):
        return agent.entrypoint
    raise CommandError(
        f"grader {grader_name!r} (llm_judge) has no `judge_entrypoint` and the agent is not "
        f"embedded ({type(agent).__name__}); add an explicit `judge_entrypoint: 'mod:fn'`"
    )


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

    def _coerce(result: object) -> AdapterResponse:
        if isinstance(result, AdapterResponse):
            return result
        if isinstance(result, str):
            return AdapterResponse(content=result)
        hint = ""
        if inspect.isawaitable(result):
            hint = (
                " — did you forget to await an async call in your entrypoint? "
                "selfevals awaits coroutines natively, so an `async def` entrypoint "
                "should return its value directly without asyncio.run()."
            )
        raise TypeError(
            f"agent entrypoint {entrypoint.raw!r} returned "
            f"{type(result).__name__}; expected str or AdapterResponse{hint}"
        )

    # Async entrypoints (`async def run(req)`) must be awaited, not called as
    # sync. Wrapping an async fn in a sync `_adapt` would hand EmbeddedAdapter a
    # bare coroutine — never awaited (RuntimeWarning) and mis-coerced to an
    # error response. Mirror the async-ness of the user callable so the str/
    # AdapterResponse coercion runs on the *resolved* value either way.
    if inspect.iscoroutinefunction(callable_obj):

        async def _adapt_async(req: AdapterRequest) -> AdapterResponse:
            return _coerce(await callable_obj(req))

        return EmbeddedAdapter(_adapt_async)

    def _adapt(req: AdapterRequest) -> AdapterResponse:
        result = callable_obj(req)
        if inspect.isawaitable(result):
            # Sync callable that returned a coroutine (e.g. a lambda wrapping an
            # async fn). EmbeddedAdapter.invoke awaits it; coerce there is moot,
            # so hand the awaitable back and let invoke resolve + type-check.
            return result  # type: ignore[return-value]
        return _coerce(result)

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
    if strategy == ProposerStrategy.LLM_PROPOSER:
        # Offline by default: deterministically applies seeded hypotheses with
        # no LLM or API key, so a spec can run end-to-end without a network.
        params = dict(experiment.proposer.parameters)
        return LLMProposer(confidence=float(params.get("confidence", 0.5)))
    raise CommandError(f"unsupported proposer strategy: {strategy}")


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


def _run_uvicorn(host: str, port: int, reload: bool) -> None:
    """Wrapper around uvicorn.run for testability — tests stub this."""
    try:
        import uvicorn
    except ImportError as exc:
        raise SelfEvalsUserError(
            "uvicorn is not installed. Install with: pip install 'selfevals[web]'"
        ) from exc
    uvicorn.run(
        "selfevals.api.app:build_app",
        host=host,
        port=port,
        reload=reload,
        factory=True,
        log_level="warning",
    )


def cmd_serve(args: argparse.Namespace) -> int:
    """Run the FastAPI API (and optionally the SvelteKit web UI) in one command.

    Without this, dogfooding meant two terminals: `python -m selfevals.api`
    in one and `npm run dev` in another. The Altman/Musk filter from
    FRONTEND_PRODUCT_PLAN.md §3 — "a dev tries it in 5 min" — fails when
    the onboarding has two processes and a proxy. One command, one URL.

    Web wiring: SvelteKit's `adapter-node` build is a Node server (it
    does SSR); we can't serve it from FastAPI directly. Instead we spawn
    `node <web-dist>/index.js` as a child process with its own port (the
    API port + 1 by default), print both URLs, and tear it down cleanly
    when uvicorn exits or the user hits Ctrl+C.
    """
    import os
    import signal
    import subprocess
    from pathlib import Path

    os.environ["SELFEVALS_DB"] = str(args.db)

    # Auto-detect a built web bundle if --web-dist wasn't given and the
    # user didn't disable web mode. Looks for `web/build/index.js`
    # relative to cwd — matches the conventional repo layout.
    web_dist: Path | None = None
    if not args.no_web:
        if args.web_dist:
            candidate = Path(args.web_dist)
            if not (candidate / "index.js").exists():
                raise SelfEvalsUserError(
                    f"--web-dist {candidate} does not contain index.js — "
                    f"run `npm run build` in the web/ dir first."
                )
            web_dist = candidate
        else:
            default_dist = Path.cwd() / "web" / "build"
            if (default_dist / "index.js").exists():
                web_dist = default_dist

    web_proc: subprocess.Popen[bytes] | None = None
    web_port = args.port + 1
    if web_dist is not None:
        web_env = os.environ.copy()
        web_env["PORT"] = str(web_port)
        web_env["ORIGIN"] = f"http://{args.host}:{web_port}"
        # The SvelteKit dev server proxies /api → 127.0.0.1:8000 via
        # vite.config.ts; the production build has no proxy, so without
        # this env var every `fetch('/api/...')` in +page.server.ts
        # would 404 against the Node server and the entire web becomes
        # unreachable (BUG-4). The hooks.server.ts handle intercepts
        # `/api/*` and forwards to this origin.
        web_env["SELFEVALS_API_BASE"] = f"http://{args.host}:{args.port}"
        try:
            web_proc = subprocess.Popen(
                ["node", str(web_dist / "index.js")],
                env=web_env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            raise SelfEvalsUserError(
                "node is not installed but --web-dist was set. Install Node "
                "(https://nodejs.org) or pass --no-web to run the API alone."
            ) from exc

    # Tear down the web child cleanly on Ctrl+C / SIGTERM.
    def _shutdown(_signum: int, _frame: object | None) -> None:
        if web_proc is not None and web_proc.poll() is None:
            web_proc.terminate()
        raise KeyboardInterrupt

    prev_sigint = signal.signal(signal.SIGINT, _shutdown)
    prev_sigterm = signal.signal(signal.SIGTERM, _shutdown)

    print("selfevals serve")
    print(f"  API : http://{args.host}:{args.port}")
    if web_proc is not None:
        print(f"  Web : http://{args.host}:{web_port}")
    else:
        print("  Web : disabled (no build at web/build/index.js; pass --web-dist)")
    print(f"  DB  : {args.db}")
    print("  ^C to stop.")

    try:
        _run_uvicorn(args.host, args.port, args.reload)
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGINT, prev_sigint)
        signal.signal(signal.SIGTERM, prev_sigterm)
        if web_proc is not None and web_proc.poll() is None:
            web_proc.terminate()
            try:
                web_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                web_proc.kill()
    return 0
