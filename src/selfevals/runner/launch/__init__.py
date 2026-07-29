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

Split by concern: `adapters.py` (agent wiring + resilience), `graders.py`
(grader factories + proposer), `datasets.py` (workspace/case/dataset
persistence). This module hosts the public `build_loop` entry point and the
process-global registry lock it serializes through.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Mapping
from typing import TYPE_CHECKING, Literal

from selfevals.graders.registry import unregister_grader
from selfevals.optimization.loop import OptimizationLoop
from selfevals.repo.loader import ExperimentSpec
from selfevals.runner.adapters import CliCommandAdapter, HttpEndpointAdapter
from selfevals.runner.executor import Executor
from selfevals.runner.launch.adapters import (
    _model_ref,
    _provider_of,
    _rate_limit_key,
    _rate_limit_key_for_agent,
    _wrap_resilience,
    _wrap_user_callable,
    agent_block_adapter_factory,
    build_adapter,
)
from selfevals.runner.launch.datasets import (
    _inline_dataset_id,
    _materialize_inline_dataset,
    _persist_cases,
    _resolve_dataset_source,
    _resolve_ref_dataset,
    ensure_workspace,
    ensure_workspace_by_id,
)
from selfevals.runner.launch.graders import (
    _agent_entrypoint_for_judge,
    _confusion_factory,
    _deterministic_factory,
    _funnel_factory,
    _judge_panel_factory,
    _llm_judge_factory,
    _pairwise_factory,
    _set_match_factory,
    build_proposer,
    register_grader_specs,
    resolve_case_graders,
)
from selfevals.runner.otlp_receiver import start_receiver
from selfevals.runner.sandbox import SandboxPolicy
from selfevals.storage.factory import object_store_base_for_storage_url
from selfevals.storage.interface import WorkspaceScope

if TYPE_CHECKING:
    from selfevals.trace.payload_router import PayloadRouter
    from selfevals.trace.span_sink import SpanSink

# Internal names re-exported so tests that reach into wiring internals
# directly (e.g. `from selfevals.runner.launch import _rate_limit_key`)
# keep working after the module→package split.
__all__ = [
    "_agent_entrypoint_for_judge",
    "_confusion_factory",
    "_deterministic_factory",
    "_funnel_factory",
    "_inline_dataset_id",
    "_judge_panel_factory",
    "_llm_judge_factory",
    "_materialize_inline_dataset",
    "_model_ref",
    "_pairwise_factory",
    "_persist_cases",
    "_provider_of",
    "_rate_limit_key",
    "_rate_limit_key_for_agent",
    "_resolve_dataset_source",
    "_resolve_ref_dataset",
    "_set_match_factory",
    "_wrap_resilience",
    "_wrap_user_callable",
    "build_adapter",
    "build_loop",
    "build_proposer",
    "ensure_workspace",
    "ensure_workspace_by_id",
    "payload_router_for_db",
    "register_grader_specs",
    "resolve_case_graders",
    "trace_sampling_override",
]

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
    Ephemeral runs skip it and the executor inlines instead."""
    from selfevals.storage.filesystem import FilesystemObjectStore
    from selfevals.trace.payload_router import PayloadRouter

    store = FilesystemObjectStore(object_store_base_for_storage_url(db_path))
    return PayloadRouter(store, workspace_id=workspace_id)


def build_loop(
    spec: ExperimentSpec,
    *,
    scope: WorkspaceScope | None,
    repetitions_per_case: int = 1,
    span_sink: SpanSink | None = None,
    payload_router: PayloadRouter | None = None,
    redis_url: str | None = None,
) -> OptimizationLoop:
    """Wire a validated spec into a runnable `OptimizationLoop`.

    Does NOT run it — the caller owns `await loop.run()` and the surrounding
    event loop, so the CLI can run it inline and the API on a thread.

    `scope` is the persistence target. Pass it (already opened on
    `spec.workspace_id`) to persist the experiment, iterations, and traces;
    pass None for an ephemeral, in-memory run (library callers only).

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
    adapter = _wrap_resilience(
        adapter,
        spec.experiment.run,
        redis_url=redis_url,
        bucket_key=_rate_limit_key(spec) if redis_url else None,
    )

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
        # Persist the experiment FIRST: eval_cases carry an experiment_id FK to
        # it, so the parent row must exist before the children. (SQLite tolerated
        # the reverse order silently; Postgres enforces the FK.)
        scope.put_entity(spec.experiment)
        _persist_cases(scope, spec)
        _materialize_inline_dataset(scope, spec)

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
    # `search_space.agents` can introduce out-of-process variants even when the
    # declared agent is embedded, so the decision has to consider both: without
    # this, a sweep that swaps an embedded default for an http variant would
    # silently lose that variant's spans.
    needs_receiver = isinstance(adapter, (HttpEndpointAdapter, CliCommandAdapter)) or any(
        str(block.get("type", "")).lower() in {"http", "cli"}
        for block in spec.experiment.search_space.agents
        if isinstance(block, Mapping)
    )
    otlp_handle = start_receiver(port=_otlp_receiver_port()) if needs_receiver else None

    # run.parallelism (schema default 8, ge=1 le=64) is the per-run concurrency
    # knob. It caps three independent fan-outs, all sized off the same value:
    #   - case_concurrency: how many CASES the loop runs at once (the dominant
    #     bottleneck — each case's adapter call is the slow I/O);
    #   - executor concurrency: how many REPETITIONS of one case run at once;
    #   - grade_concurrency: how many (rep, grader) pairs the loop scores at once.
    # Before case_concurrency existed, cases ran strictly in series regardless of
    # this value (it only bounded intra-case work), so a 50-case run took
    # 50x a single case even at parallelism=8.
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
    # Enables `search_space.agents`: proposals carrying an `agent` block get an
    # adapter built (and cached) on demand instead of the declared default.
    executor.set_adapter_factory(agent_block_adapter_factory(spec, redis_url=redis_url))
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
        case_concurrency=parallelism,
    )
