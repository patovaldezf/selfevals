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
import logging
import os
import threading
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Literal, cast

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
from selfevals.repo.datasets import build_dataset
from selfevals.repo.loader import (
    AgentEntrypoint,
    AgentModelDecl,
    AgentSpec,
    CliAgentSpec,
    EmbeddedAgentSpec,
    ExperimentSpec,
    HttpAgentSpec,
    InlineDatasetSource,
    LoaderError,
    RefDatasetSource,
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
from selfevals.runner.otlp_receiver import start_receiver
from selfevals.runner.sandbox import SandboxPolicy
from selfevals.schemas._base import EntityRef
from selfevals.schemas.dataset import Dataset, SplitAllocation
from selfevals.schemas.enums import ProposerStrategy
from selfevals.schemas.eval_case import EvalCase
from selfevals.schemas.experiment import Experiment
from selfevals.schemas.fleet import ModelRef
from selfevals.schemas.workspace import Workspace
from selfevals.storage.factory import object_store_base_for_storage_url
from selfevals.storage.interface import ListFilter, StorageInterface, WorkspaceScope

if TYPE_CHECKING:
    from selfevals.trace.payload_router import PayloadRouter
    from selfevals.trace.span_sink import SpanSink

logger = logging.getLogger(__name__)

# Serializes the register → resolve → unregister window so concurrent
# `build_loop` calls cannot trample one another's grader registrations.
_REGISTRY_LOCK = threading.Lock()

_TRACE_SAMPLING_ENV = "SELFEVALS_TRACE_SAMPLING"
# Tolerate both the FE's vocabulary (`all` / `failures-only`) and the spec's
# (`all` / `failed` / `none`) — they mean the same persistence policy.
_TRACE_SAMPLING_ALIASES: dict[str, Literal["none", "all", "failed"]] = {
    "all": "all",
    "failures-only": "failed",
    "failures_only": "failed",
    "failed": "failed",
    "none": "none",
}


def trace_sampling_override() -> Literal["none", "all", "failed"] | None:
    """Read `SELFEVALS_TRACE_SAMPLING` into a `persist_traces` value, or None.

    Lets an operator force the trace-persistence policy process-wide without
    editing every spec — the FE asked for this so it can run with `all` against a
    server it doesn't author specs for. Accepts the FE's `failures-only` spelling
    as well as the spec's `failed`. An unset or unrecognized value returns None
    (the spec's own `persist_traces` wins). Precedence at the call sites is:
    explicit request override > this env var > spec default."""
    raw = os.environ.get(_TRACE_SAMPLING_ENV)
    if raw is None:
        return None
    return _TRACE_SAMPLING_ALIASES.get(raw.strip().lower())


_OTLP_PORT_ENV = "SELFEVALS_OTLP_PORT"


def _otlp_receiver_port() -> int:
    """Port for the embedded OTLP receiver. 0 (default) = OS-assigned dynamic
    port, one per run — the right choice for concurrent runs. Set
    `SELFEVALS_OTLP_PORT` to pin a stable port so a long-lived out-of-process
    agent can configure its OTLP exporter once. An invalid value falls back to 0
    rather than crashing the run."""
    raw = os.environ.get(_OTLP_PORT_ENV)
    if not raw:
        return 0
    try:
        port = int(raw.strip())
    except ValueError:
        logger.warning("ignoring invalid %s=%r (want an int port)", _OTLP_PORT_ENV, raw)
        return 0
    return port if 0 <= port <= 65535 else 0


def payload_router_for_db(db_path: str, workspace_id: str) -> PayloadRouter:
    """Build a `PayloadRouter` for the configured storage URL/path.

    SQLite stores objects next to the db. Postgres uses the local
    ``SELFEVALS_OBJECTS_DIR`` override or ``./objects`` until S3 lands. Callers
    that persist (`selfevals run`, the HTTP run launcher) pass the result into
    `build_loop` so the executor offloads large trace payloads; ephemeral
    `--no-persist` runs skip it and the executor inlines instead."""
    from selfevals.storage.filesystem import FilesystemObjectStore
    from selfevals.trace.payload_router import PayloadRouter

    store = FilesystemObjectStore(object_store_base_for_storage_url(db_path))
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
        kwargs: dict[str, object] = {"env": agent.env, "model": _model_ref(agent.model)}
        if agent.timeout_seconds is not None:
            kwargs["timeout_seconds"] = agent.timeout_seconds
        return CliCommandAdapter(agent.command, **kwargs)  # type: ignore[arg-type]
    if isinstance(agent, HttpAgentSpec):
        http_kwargs: dict[str, object] = {
            "headers": agent.headers,
            "model": _model_ref(agent.model),
        }
        if agent.timeout_seconds is not None:
            http_kwargs["timeout_seconds"] = agent.timeout_seconds
        return HttpEndpointAdapter(agent.url, **http_kwargs)  # type: ignore[arg-type]
    raise SelfEvalsUserError(f"unsupported agent spec: {type(agent).__name__}")  # defensive


def _model_ref(decl: AgentModelDecl | None) -> ModelRef | None:
    """Lift the loader's `agent.model` declaration into a `ModelRef`, or None."""
    if decl is None:
        return None
    return ModelRef(provider=decl.provider, name=decl.name)


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
        if g_spec.type == "judge_panel":
            entry = g_spec.judge_entrypoint or _agent_entrypoint_for_judge(g_spec.name, spec.agent)
            try:
                judge_callable = resolve_agent_callable(entry)
            except LoaderError as exc:
                raise SelfEvalsUserError(str(exc)) from exc
            judge_adapter = _wrap_user_callable(judge_callable, entry)
            register_grader(
                g_spec.name,
                _judge_panel_factory(
                    g_spec.name,
                    judge_adapter,
                    g_spec.rubric or "",
                    n_judges=g_spec.n_judges or 3,
                    consensus=g_spec.consensus or "majority",
                ),
            )
            registered.append(g_spec.name)
            continue
        if g_spec.type == "set_match":
            register_grader(g_spec.name, _set_match_factory(g_spec.name, g_spec.params))
            registered.append(g_spec.name)
            continue
        if g_spec.type == "funnel":
            register_grader(g_spec.name, _funnel_factory(g_spec.name, g_spec.params))
            registered.append(g_spec.name)
            continue
        if g_spec.type == "confusion":
            register_grader(g_spec.name, _confusion_factory(g_spec.name, g_spec.params))
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


def _judge_panel_factory(
    name: str,
    judge_adapter: AgentAdapter,
    rubric: str,
    *,
    n_judges: int,
    consensus: str,
) -> Callable[[], Grader]:
    """Build a panel of `n_judges` identical-rubric judges + a consensus rule.

    The judges share one rubric and one adapter — independence comes from the
    LLM's own sampling, not from distinct prompts. Each judge needs a unique
    name within the panel (a `JudgePanelGrader` invariant), so they are suffixed
    `-judge-0..N`. Default consensus is `majority`; for `weighted` (which the
    panel requires explicit weights for) we pass uniform weights so a bare
    `consensus: weighted` still constructs.
    """
    from selfevals.graders.judge_panel import JudgePanelGrader

    template = RubricTemplate(rubric=rubric)

    def _build() -> Grader:
        judges: list[Grader] = [
            LLMJudgeGrader(name=f"{name}-judge-{k}", judge_adapter=judge_adapter, rubric=template)
            for k in range(n_judges)
        ]
        weights = [1.0] * n_judges if consensus == "weighted" else None
        return JudgePanelGrader(name=name, judges=judges, consensus_rule=consensus, weights=weights)

    return _build


def _set_match_factory(name: str, params: dict[str, object]) -> Callable[[], Grader]:
    from selfevals.graders.set_match import GatingDimension, SetMatchGrader

    gating = cast("GatingDimension", params.get("gating", "completeness"))
    threshold = float(cast(float, params.get("threshold", 1.0)))
    case_sensitive = bool(params.get("case_sensitive", False))

    def _build() -> Grader:
        return SetMatchGrader(
            name=name, gating=gating, threshold=threshold, case_sensitive=case_sensitive
        )

    return _build


def _confusion_factory(name: str, params: dict[str, object]) -> Callable[[], Grader]:
    from selfevals.graders.classification import ClassificationGrader

    extract = str(params.get("extract", "label"))
    expected_from_raw = params.get("expected_from")
    expected_from = str(expected_from_raw) if expected_from_raw is not None else None
    case_sensitive = bool(params.get("case_sensitive", False))

    def _build() -> Grader:
        return ClassificationGrader(
            name=name,
            extract=extract,
            expected_from=expected_from,
            case_sensitive=case_sensitive,
        )

    return _build


def _funnel_factory(name: str, params: dict[str, object]) -> Callable[[], Grader]:
    """Build a FunnelGrader from the loader's validated level dicts.

    Level construction happens inside `_build()` (not here) so each
    `resolve_case_graders` call gets fresh instances and so registry lookups for
    nested `grader:` references resolve after every factory has registered —
    declaration order between the funnel and the graders it references does not
    matter (mirrors how `resolve_case_graders` instantiates lazily).
    """
    from selfevals.graders.funnel import (
        FunnelGrader,
        _ByIndexMatch,
        _ByKeyMatch,
        _EqualsMatch,
        _ExistsMatch,
        _Level,
        _SpanExistsMatch,
        _ToolCalledMatch,
    )
    from selfevals.graders.set_match import GatingDimension, SetMatchGrader
    from selfevals.schemas.enums import SpanKind

    levels_spec = cast("list[dict[str, object]]", params.get("levels", []))

    def _default_fm(key: str, kind: str) -> str:
        suffix = {
            "exists": "absent",
            "equals": "mismatch",
            "by_key": "key_mismatch",
            "by_index": "index_mismatch",
            "tool_called": "tool_absent",
            "span_exists": "span_absent",
        }.get(kind, "fail")
        return f"funnel_{key}_{suffix}"

    def _build_match(key: str, extract: str, match: dict[str, object]) -> Grader:
        match_name = f"{name}.{key}"
        if "grader" in match:
            ref = cast(str, match["grader"])
            return resolve_graders([ref])[0]
        kind = cast(str, match["kind"])
        cs = bool(match.get("case_sensitive", False))
        fm = _default_fm(key, kind)
        if kind == "set_match":
            gating = cast("GatingDimension", match.get("gating", "completeness"))
            threshold = float(cast(float, match.get("threshold", 1.0)))
            # An omitted level `extract` ("") means "the default detected slot"
            # for set_match, matching the standalone grader's `extract="detected"`
            # default — without this a bare `kind: set_match` level would select
            # the root dict and always FAIL.
            sm_extract = extract or "detected"
            return SetMatchGrader(
                name=match_name,
                gating=gating,
                threshold=threshold,
                case_sensitive=cs,
                extract=sm_extract,
            )
        if kind == "exists":
            return _ExistsMatch(match_name, extract=extract, failure_mode=fm)
        if kind == "equals":
            return _EqualsMatch(
                match_name,
                extract=extract,
                value=match["value"],
                case_sensitive=cs,
                failure_mode=fm,
            )
        if kind == "by_key":
            return _ByKeyMatch(
                match_name,
                extract=extract,
                key=cast(str, match["key"]),
                value=match["value"],
                case_sensitive=cs,
                failure_mode=fm,
            )
        if kind == "by_index":
            return _ByIndexMatch(
                match_name,
                extract=extract,
                index=cast(int, match["index"]),
                value=match["value"],
                case_sensitive=cs,
                failure_mode=fm,
            )
        if kind == "tool_called":
            return _ToolCalledMatch(match_name, tool=cast(str, match["tool"]), failure_mode=fm)
        if kind == "span_exists":
            return _SpanExistsMatch(
                match_name, span_kind=SpanKind(cast(str, match["span_kind"])), failure_mode=fm
            )
        raise SelfEvalsUserError(f"funnel: unknown match kind {kind!r}")  # defensive

    def _build_level(node: dict[str, object]) -> _Level:
        key = cast(str, node["key"])
        extract = cast(str, node.get("extract", ""))
        match = _build_match(key, extract, cast("dict[str, object]", node["match"]))
        children_spec = cast("list[dict[str, object]]", node.get("children", []))
        return _Level(
            key=key,
            extract=extract,
            match=match,
            gate=bool(node.get("gate", False)),
            failure_mode=cast("str | None", node.get("failure_mode")),
            feeds_extract=bool(node.get("feeds_extract", False)),
            children=[_build_level(c) for c in children_spec],
        )

    def _build() -> Grader:
        return FunnelGrader(name=name, levels=[_build_level(node) for node in levels_spec])

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


def ensure_workspace_by_id(storage: StorageInterface, workspace_id: str) -> None:
    """Make sure a workspace row exists for `workspace_id`. Idempotent.

    The experiment-free variant of `ensure_workspace`, used by standalone flows
    (uploading a dataset) that have a workspace id but no `ExperimentSpec`.
    """
    with storage.open(workspace_id) as s:
        if s.exists(Workspace, workspace_id):
            return
    ws = Workspace(
        id=workspace_id,
        workspace_id=workspace_id,
        slug=workspace_id.lower(),
        name=workspace_id,
    )
    with storage.open(workspace_id) as s:
        s.put_entity(ws)


def ensure_workspace(storage: StorageInterface, spec: ExperimentSpec) -> None:
    """Make sure the workspace row exists. Idempotent."""
    ensure_workspace_by_id(storage, spec.workspace_id)


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


def _resolve_dataset_source(
    scope: WorkspaceScope | None, spec: ExperimentSpec
) -> SplitAllocation | None:
    """Resolve the experiment's dataset source, returning its split allocation.

    * Inline: cases already live in `spec.cases`; the split (if any) rides the
      block. Returns it so the loop's sampler honors it.
    * Ref: load the persisted `Dataset`, hydrate `spec.cases` in place from its
      case refs, and return its split. A ref needs storage — resolving one
      without a `scope` (an ephemeral `--no-persist` run) is a user error.

    Mutates `spec.cases` in place (it's a list on a frozen dataclass) so every
    downstream reader — graders, persistence, the loop — sees the resolved set.
    """
    source = spec.dataset_source
    if isinstance(source, InlineDatasetSource):
        return source.split_allocation
    if isinstance(source, RefDatasetSource):
        if scope is None:
            raise SelfEvalsUserError(
                f"experiment references dataset {source.ref.id!r} but the run is not "
                "persisting — a dataset reference needs storage to resolve. Drop "
                "--no-persist, or declare cases inline."
            )
        return _resolve_ref_dataset(scope, spec, source.ref)
    return None  # defensive: unknown source variant


def _resolve_ref_dataset(
    scope: WorkspaceScope, spec: ExperimentSpec, ref: EntityRef
) -> SplitAllocation | None:
    try:
        dataset = scope.get_entity(Dataset, ref.id)
    except Exception as exc:
        raise SelfEvalsUserError(
            f"experiment references dataset {ref.id!r}, which was not found in "
            f"workspace {spec.workspace_id!r}. Create it first "
            "(`selfevals dataset create` or POST .../datasets)."
        ) from exc
    assert isinstance(dataset, Dataset)

    ref_ids = {c.id for c in dataset.cases}
    resolved = [
        c
        for c in scope.list_entities(EvalCase, ListFilter())
        if isinstance(c, EvalCase) and c.id in ref_ids
    ]
    if not resolved:
        raise SelfEvalsUserError(
            f"dataset {ref.id!r} resolved to zero cases — its EvalCases are missing "
            "from storage. Re-import the dataset."
        )
    # Hydrate the (frozen-dataclass) spec's case list in place.
    spec.cases[:] = resolved
    # Stamp the experiment's optimization ref so reports/links point at the
    # actual dataset (the YAML may have named a placeholder).
    spec.experiment.datasets.optimization = EntityRef(id=dataset.id, version=dataset.version)
    spec.experiment.frozen.datasets = [EntityRef(id=dataset.id, version=dataset.version)]
    return dataset.split_allocation


def _materialize_inline_dataset(scope: WorkspaceScope, spec: ExperimentSpec) -> None:
    """Persist a real Dataset over an inline experiment's cases and link it.

    The `dataset:` block's inline cases used to vanish into the run with no
    Dataset entity — leaving `experiment.datasets.optimization` pointing at a
    placeholder that never existed. Here we materialize one: build a Dataset
    over the just-persisted cases (manifest hash + statistics) and rewrite the
    experiment's dataset refs (`datasets.optimization`, `frozen.datasets[0]`) to
    point at it. Ref-sourced experiments are resolved elsewhere (F6) and skipped.

    Idempotent across reruns: the dataset id is derived from the experiment id,
    so a second launch updates the same Dataset row in place rather than
    spawning a new one. Cases are already in storage (`_persist_cases`), so this
    only writes the manifest.
    """
    source = spec.dataset_source
    if not isinstance(source, InlineDatasetSource) or not spec.cases:
        return

    dataset_id = _inline_dataset_id(spec.experiment.id)
    dataset_type = source.dataset_type
    if dataset_type is None:
        # No declared type — adopt the cases' shared dataset_type (they all carry
        # one via taxonomy; inline suites are typically homogeneous).
        dataset_type = spec.cases[0].taxonomy.dataset_type
    name = source.name or f"{spec.experiment.name} dataset"

    existing_version = 1
    try:
        prior = scope.get_entity(Dataset, dataset_id)
        if isinstance(prior, Dataset):
            existing_version = prior.version
    except Exception:
        existing_version = 0  # not yet persisted → fresh insert at version 1

    dataset = build_dataset(
        workspace_id=spec.workspace_id,
        name=name,
        dataset_type=dataset_type,
        cases=spec.cases,
        description=source.description,
        split_allocation=source.split_allocation,
        dataset_id=dataset_id,
    )
    if existing_version:
        dataset.version = existing_version
    scope.put_entity(dataset)

    ref = EntityRef(id=dataset.id, version=dataset.version)
    spec.experiment.datasets.optimization = ref
    spec.experiment.frozen.datasets = [ref]


def _inline_dataset_id(experiment_id: str) -> str:
    """A stable Dataset id for an experiment's inline cases (idempotent reruns).

    Reuses the experiment's ULID suffix under the `ds_` prefix so the id is
    valid (prefixed ULID shape) and 1:1 with the experiment — relaunching the
    same experiment rewrites the same Dataset row.
    """
    suffix = experiment_id.split("_", 1)[1] if "_" in experiment_id else experiment_id
    return f"ds_{suffix}"


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

    # Resolve a `ref:` dataset before anything reads `spec.cases` (graders,
    # persistence, the loop). For inline sources this is a no-op — cases are
    # already in `spec.cases` and the split comes from the block.
    split_allocation = _resolve_dataset_source(scope, spec)

    with _REGISTRY_LOCK:
        registered = register_grader_specs(spec)
        try:
            graders = resolve_case_graders(spec.cases)
        finally:
            for name in registered:
                unregister_grader(name)

    if scope is not None:
        _persist_cases(scope, spec)
        _materialize_inline_dataset(scope, spec)
        scope.put_entity(spec.experiment)

    # Start an embedded OTLP receiver only for out-of-process agents (cli/http):
    # those run in a separate process and can export their own spans (LLM calls,
    # chains) to us so they nest under each case's trace. Embedded agents share
    # this process, so they need no receiver. The executor closes it (loop's
    # finally → executor.close()).
    #
    # Port: dynamic (OS-assigned) by default — correct for concurrent runs, each
    # gets its own receiver/port. Set SELFEVALS_OTLP_PORT to pin it when a
    # long-lived agent server (e.g. an HTTP adapter) must point a single OTLP
    # exporter at a STABLE endpoint it configures once. A fixed port assumes runs
    # don't overlap (the bind would clash, and a shared receiver has one recorder
    # slot); use the default dynamic port if you run experiments concurrently.
    otlp_handle = (
        start_receiver(port=_otlp_receiver_port())
        if isinstance(adapter, (HttpEndpointAdapter, CliCommandAdapter))
        else None
    )

    # run.parallelism (schema default 1, ge=1 le=64) is the per-run concurrency
    # knob. Until now it was dead code: the executor and loop used their own
    # hardcoded default of 8. Wiring it here makes the YAML field load-bearing —
    # `parallelism` caps both how many cases the executor runs at once and how
    # many graders the loop scores in parallel. NOTE: this changes the default
    # behavior from a fixed 8 to `run.parallelism` (default 1). See the SF-3 PR.
    parallelism = spec.experiment.run.parallelism
    executor = Executor(
        adapter=adapter,
        sandbox=SandboxPolicy(spec.experiment.run.sandbox),
        workspace_id=spec.workspace_id,
        span_sink=span_sink,
        payload_router=payload_router,
        otlp_handle=otlp_handle,
        concurrency=parallelism,
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
        split_allocation=split_allocation,
        grade_concurrency=parallelism,
    )
