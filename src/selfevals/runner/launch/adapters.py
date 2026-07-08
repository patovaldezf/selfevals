"""Wire a transport-tagged `AgentSpec` into a concrete `AgentAdapter`.

Also owns the resilience wrapping (retry + rate-limit) applied per-run, and
the fleet-shared rate-limit key derivation.
"""

from __future__ import annotations

import inspect

from selfevals._errors import SelfEvalsUserError
from selfevals.repo.loader import (
    AgentEntrypoint,
    AgentModelDecl,
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
from selfevals.runner.retry import RetryingAdapter, RetryPolicy
from selfevals.runner.throttle import AsyncTokenBucket, RateLimitedAdapter, TokenBucket
from selfevals.schemas.experiment import RunSpec
from selfevals.schemas.fleet import ModelRef


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


def _provider_of(agent: AgentSpec) -> str | None:
    """The provider declared on the agent's model, or None (embedded agents)."""
    model = getattr(agent, "model", None)
    if model is None:
        return None
    return getattr(model, "provider", None)


def _rate_limit_key(spec: ExperimentSpec) -> str:
    """Stable, fleet-shared key for this run's token bucket.

    Keyed by (workspace, provider) so every worker of every experiment that
    targets the same provider in the same workspace draws from ONE global quota
    — which is how provider rate limits are actually billed. Keying by
    experiment alone would let two concurrent experiments each get the full RPM
    and blow the real limit; keying by provider alone would leak across tenants.
    Falls back to the experiment id when the agent declares no provider
    (embedded agents), so such runs still get a per-experiment global cap rather
    than colliding on a shared 'unknown' key."""
    provider = _provider_of(spec.agent)
    if provider is None:
        return f"selfevals:ratelimit:{spec.workspace_id}:exp:{spec.experiment.id}"
    return f"selfevals:ratelimit:{spec.workspace_id}:provider:{provider}"


def _wrap_resilience(
    adapter: AgentAdapter,
    run: RunSpec,
    *,
    redis_url: str | None = None,
    bucket_key: str | None = None,
) -> AgentAdapter:
    """Wrap the adapter with retry (inner) and rate-limit (outer) per `run`.

    Order matters: the throttle is outermost so every physical request — first
    try or retry — passes the bucket; if retry sat outside, a backoff storm could
    bypass the limit and re-trigger 429s. Retry defaults ON (`max_retries=2`);
    rate-limit is OFF unless `requests_per_minute` is set (we can't guess the
    user's provider tier).

    The bucket is GLOBAL (Redis-backed) when `redis_url` and `bucket_key` are
    threaded in — which only happens on the distributed worker path, where N
    workers must share one quota. Otherwise it's in-process (`AsyncTokenBucket`),
    shared across every case of this one run (the executor holds this single
    adapter). The local CLI path passes neither, so it never touches Redis — a
    structural guarantee, not an env-var accident. Two runs sharing a Redis key
    may declare different `rpm`; that's accepted for v1 (rate goes as ARGV per
    call, so the current call's rate wins)."""
    if run.retry.max_retries > 0:
        adapter = RetryingAdapter(
            adapter,
            RetryPolicy(
                max_retries=run.retry.max_retries,
                base_delay=run.retry.base_delay_seconds,
                max_delay=run.retry.max_delay_seconds,
                multiplier=run.retry.multiplier,
                jitter=run.retry.jitter,
            ),
        )
    rpm = run.rate_limit.requests_per_minute
    if rpm is not None:
        rate_per_sec = rpm / 60.0
        capacity = float(run.rate_limit.burst) if run.rate_limit.burst else max(1.0, rate_per_sec)
        bucket: TokenBucket
        if redis_url is not None and bucket_key is not None:
            from selfevals.runner.redis_throttle import RedisTokenBucket

            bucket = RedisTokenBucket(
                redis_url=redis_url,
                key=bucket_key,
                rate_per_sec=rate_per_sec,
                capacity=capacity,
            )
        else:
            bucket = AsyncTokenBucket(rate_per_sec=rate_per_sec, capacity=capacity)
        adapter = RateLimitedAdapter(adapter, bucket)
    return adapter


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
