"""Canonical wiring from an `ExperimentSpec` to a runnable `OptimizationLoop`.

This is the single place that turns a validated spec into the object graph the
optimization loop needs — adapter, proposer, graders, executor, scope. Both
entry points reuse it:

* the CLI (`selfevals run`) builds the loop and runs it synchronously;
* the HTTP API (`POST .../experiments/run`) builds the loop and runs it on a
  background thread.

Keeping it here (in `runner/`, alongside `Executor`/adapters) rather than in
`cli/` means the API does not have to import from the CLI — neither frontend
depends on the other.

Graders referenced by YAML name are registered in a process-global registry
(`graders.registry`). That registry is only consulted *synchronously* while
building the loop: `resolve_case_graders` instantiates every grader up front and
hands the loop a concrete `list[Grader]`. So `build_loop` registers the
spec's graders, resolves them, and unregisters — all under a lock — before it
returns. Two concurrent runs (e.g. two API requests) therefore never see each
other's registrations, and the returned graders are immune to any later registry
mutation. This is what makes background runs safe to overlap.
"""

from __future__ import annotations

import inspect
import threading
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from selfevals._errors import SelfEvalsUserError
from selfevals.graders.base import Grader
from selfevals.graders.deterministic import DeterministicGrader
from selfevals.graders.llm_judge import LLMJudgeGrader, RubricTemplate
from selfevals.graders.registry import (
    available_graders,
    register_grader,
    resolve_graders,
    unregister_grader,
)
from selfevals.optimization.loop import OptimizationLoop
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
from selfevals.runner.adapters import (
    AdapterRequest,
    AdapterResponse,
    AgentAdapter,
    CliCommandAdapter,
    EmbeddedAdapter,
    HttpEndpointAdapter,
)
from selfevals.runner.executor import Executor
from selfevals.runner.sandbox import SandboxPolicy
from selfevals.schemas.enums import ProposerStrategy
from selfevals.schemas.experiment import Experiment
from selfevals.schemas.workspace import Workspace
from selfevals.storage.interface import WorkspaceScope
from selfevals.storage.sqlite import SQLiteStorage

if TYPE_CHECKING:
    from selfevals.trace.payload_router import PayloadRouter
    from selfevals.trace.span_sink import SpanSink

# Serializes the register → resolve → unregister window so concurrent
# `build_loop` calls cannot trample one another's grader registrations.
_REGISTRY_LOCK = threading.Lock()


def payload_router_for_db(db_path: str, workspace_id: str) -> PayloadRouter:
    """Build a `PayloadRouter` whose object store sits next to the SQLite db.

    Same layout the analyze CLI and the HTTP `/payloads` endpoint use
    (`<db>.parent/objects`), so a pointer written here resolves there. Callers
    that persist (`selfevals run`, the HTTP run launcher) pass the result into
    `build_loop` so the executor offloads large trace payloads; ephemeral
    `--no-persist` runs skip it and the executor inlines instead."""
    from pathlib import Path

    from selfevals.storage.filesystem import FilesystemObjectStore
    from selfevals.trace.payload_router import PayloadRouter

    store = FilesystemObjectStore(Path(db_path).parent / "objects")
    return PayloadRouter(store, workspace_id=workspace_id)


def build_adapter(agent: AgentSpec) -> AgentAdapter:
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
            raise SelfEvalsUserError(str(exc)) from exc
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
    raise SelfEvalsUserError(f"unsupported agent spec: {type(agent).__name__}")  # defensive


def build_proposer(experiment: Experiment) -> Proposer:
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
            raise SelfEvalsUserError(
                "manual proposer requires proposer.parameters.proposals as a non-empty list"
            )
        return ManualProposer(manual)
    if strategy == ProposerStrategy.LLM_PROPOSER:
        # Offline by default: deterministically applies seeded hypotheses with
        # no LLM or API key, so a spec can run end-to-end without a network.
        params = dict(experiment.proposer.parameters)
        return LLMProposer(confidence=float(params.get("confidence", 0.5)))
    raise SelfEvalsUserError(f"unsupported proposer strategy: {strategy}")


def register_grader_specs(spec: ExperimentSpec) -> list[str]:
    """Register YAML-declared graders into the global registry.

    Returns the list of names that were registered so the caller can
    unregister them when done — keeping the registry hermetic across
    consecutive runs.

    Deterministic specs override the default factory with one that uses the
    declared name. LLM-judge specs require a `judge_entrypoint` (or fall back to
    the agent entrypoint) so the rubric grader can invoke a real callable.
    """
    registered: list[str] = []
    for g_spec in spec.graders:
        if g_spec.type == "deterministic":
            register_grader(g_spec.name, _deterministic_factory(g_spec.name))
            registered.append(g_spec.name)
            continue
        if g_spec.type == "llm_judge":
            entry = g_spec.judge_entrypoint or _agent_entrypoint_for_judge(g_spec.name, spec.agent)
            try:
                judge_callable = resolve_agent_callable(entry)
            except LoaderError as exc:
                raise SelfEvalsUserError(str(exc)) from exc
            judge_adapter = _wrap_user_callable(judge_callable, entry)
            rubric = g_spec.rubric or ""
            register_grader(g_spec.name, _llm_judge_factory(g_spec.name, judge_adapter, rubric))
            registered.append(g_spec.name)
            continue
        raise SelfEvalsUserError(f"unsupported grader type: {g_spec.type!r}")  # defensive
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


def resolve_case_graders(cases: Sequence[object]) -> list[Grader]:
    """Build the grader list the loop will run for every case.

    The default behaviour (no case declares a `graders:` list) is unchanged
    from the original `[DeterministicGrader()]`. As soon as a case names graders
    by string we route through the registry, which is the codepath that raises
    the "not registered" friendly error.
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
    # listing the registry contents — the user-facing "unknown grader" path.
    _ = available_graders()  # cheap, also guarantees registry import side effects.
    return list(resolve_graders(referenced))


def _agent_entrypoint_for_judge(grader_name: str, agent: AgentSpec) -> AgentEntrypoint:
    """Resolve the judge fallback to the agent's entrypoint.

    The `judge_entrypoint`-omitted fallback only makes sense for an embedded
    agent — a cli/http agent has no in-process callable to reuse as a judge.
    """
    if isinstance(agent, EmbeddedAgentSpec):
        return agent.entrypoint
    raise SelfEvalsUserError(
        f"grader {grader_name!r} (llm_judge) has no `judge_entrypoint` and the agent is not "
        f"embedded ({type(agent).__name__}); add an explicit `judge_entrypoint: 'mod:fn'`"
    )


def _wrap_user_callable(callable_obj: object, entrypoint: AgentEntrypoint) -> AgentAdapter:
    """Adapt the user's function into an AgentAdapter.

    Accepted return types from the user's callable:
    - AdapterResponse: passed through.
    - str: wrapped as `AdapterResponse(content=...)`.
    Anything else raises at invoke-time with a clear message — we don't silently
    coerce dicts or numbers because the grader semantics depend on a textual
    response.
    """
    if not callable(callable_obj):
        raise SelfEvalsUserError(
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
    # sync. Mirror the async-ness of the user callable so the str/AdapterResponse
    # coercion runs on the *resolved* value either way.
    if inspect.iscoroutinefunction(callable_obj):

        async def _adapt_async(req: AdapterRequest) -> AdapterResponse:
            return _coerce(await callable_obj(req))

        return EmbeddedAdapter(_adapt_async)

    def _adapt(req: AdapterRequest) -> AdapterResponse:
        result = callable_obj(req)
        if inspect.isawaitable(result):
            # Sync callable that returned a coroutine (e.g. a lambda wrapping an
            # async fn). EmbeddedAdapter.invoke awaits it.
            return result  # type: ignore[return-value]
        return _coerce(result)

    return EmbeddedAdapter(_adapt)


def ensure_workspace(storage: SQLiteStorage, spec: ExperimentSpec) -> None:
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


def _persist_cases(scope: WorkspaceScope, spec: ExperimentSpec) -> None:
    """Persist the run's eval cases, stamped with the experiment id.

    Cases are an authoring-time construct (they live in the `ExperimentSpec`,
    not storage) — so without this, a launched experiment leaves no trace of
    *which* cases it ran, and `GET .../experiments/{id}/cases` has nothing to
    list. We stamp `experiment_id` (the loader leaves it None) and persist
    every case, holdout included: the cases endpoint reports the full set and
    flags holdout, rather than hiding reserved cases.

    Idempotent across reruns of the same experiment: `EvalCase` ids are stable
    within a spec (the loader assigns them once), so `put_entity` updates the
    existing row in place rather than duplicating.
    """
    for case in spec.cases:
        if case.experiment_id != spec.experiment.id:
            case.experiment_id = spec.experiment.id
        scope.put_entity(case)


def build_loop(
    spec: ExperimentSpec,
    *,
    scope: WorkspaceScope | None,
    repetitions_per_case: int = 1,
    span_sink: SpanSink | None = None,
    payload_router: PayloadRouter | None = None,
) -> OptimizationLoop:
    """Wire a validated spec into a runnable `OptimizationLoop`.

    Does NOT run it — the caller owns `await loop.run()` and the surrounding
    event loop, so the CLI can run it inline and the API on a thread.

    `scope` is the persistence target. Pass it (already opened on
    `spec.workspace_id`) to persist the experiment, iterations, and traces;
    pass None for an ephemeral, in-memory run (the CLI's `--no-persist`).

    `span_sink` taps every span the run produces for live streaming (SSE).
    Omit it (the CLI path) and the executor uses a no-op sink — zero overhead.
    `selfevals serve` passes a broker-backed sink so `/stream` subscribers see
    spans as they happen.

    The grader registry is touched only inside this call, under a lock: register
    the spec's graders, instantiate them, unregister. The returned loop holds
    concrete grader instances, so concurrent runs never interfere.
    """
    # Local import avoids a module-load cycle: matrix → ... → launch.
    from selfevals.decision.matrix import DecisionMatrixEvaluator

    adapter = build_adapter(spec.agent)
    proposer = build_proposer(spec.experiment)

    with _REGISTRY_LOCK:
        registered = register_grader_specs(spec)
        try:
            graders = resolve_case_graders(spec.cases)
        finally:
            for name in registered:
                unregister_grader(name)

    if scope is not None:
        scope.put_entity(spec.experiment)
        _persist_cases(scope, spec)

    executor = Executor(
        adapter=adapter,
        sandbox=SandboxPolicy(spec.experiment.run.sandbox),
        workspace_id=spec.workspace_id,
        span_sink=span_sink,
        payload_router=payload_router,
    )
    return OptimizationLoop(
        experiment=spec.experiment,
        executor=executor,
        proposer=proposer,
        graders=graders,
        cases=spec.cases,
        scope=scope,
        decision_evaluator=DecisionMatrixEvaluator(),
        repetitions_per_case=repetitions_per_case,
    )
