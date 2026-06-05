"""Agent adapters: how selfevals invokes the agent under test.

`AgentAdapter` is the narrowest contract: given an `AdapterRequest` (the
case input and a frozen set of allowed tools), return an `AdapterResponse`
with the agent's reply plus any tool calls it made and token accounting.

The recorder ingests these into a Trace separately — adapters do not own
Trace assembly. This keeps adapters small and lets us swap them out for
mocks in tests.

`invoke` is async — it is the one contract, and it awaits I/O natively.

Three concrete adapters ship:

- `EmbeddedAdapter` — wraps a plain Python callable (sync or async).
  Useful for tests and for fast-iteration in-repo agents.
- `CliCommandAdapter` — runs a subprocess with JSON-over-stdio.
- `HttpEndpointAdapter` — POSTs JSON to a URL (e.g. an ngrok tunnel).

The CLI and HTTP adapters keep a deliberately small surface: the
protocol is nailed down, retries/streaming/auth-header policy is left to
the caller.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from selfevals.schemas.fleet import Agent, ModelRef

EmbeddedCallable = Callable[
    ["AdapterRequest"], "AdapterResponse | Awaitable[AdapterResponse]"
]
"""A wrapped agent callable. May be sync (returns an AdapterResponse) or
async (returns an awaitable of one)."""


class AdapterError(RuntimeError):
    """Generic adapter failure (transport, decoding, contract violation)."""


@dataclass(frozen=True)
class AdapterToolUse:
    tool: str
    tool_use_id: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AdapterRequest:
    workspace_id: str
    case_id: str
    input: dict[str, Any]
    """Pydantic-compatible payload from `EvalCase.input` (messages, etc.)."""

    context: dict[str, Any] | None = None
    tools_allowed: list[str] = field(default_factory=list)
    parameters: dict[str, Any] = field(default_factory=dict)
    """Overrides applied by the proposer (model temperature, system prompt
    swap, etc.). Adapters receive these verbatim and pass them through
    to the underlying runtime."""

    metadata: dict[str, Any] = field(default_factory=dict)

    def get_model_param(self, key: str, default: Any = None) -> Any:
        """Read a model param from the proposer envelope `parameters["model_params"]`.

        Grid/random/llm proposers wrap their search-space params under a
        ``model_params`` key (the namespace the editable contract gates). This
        helper flattens that envelope so adapters don't hard-code its shape.
        """
        inner = (self.parameters or {}).get("model_params") or {}
        return inner.get(key, default)


@dataclass(frozen=True)
class AdapterResponse:
    content: str | None
    structured_output: dict[str, Any] | None = None
    tool_uses: list[AdapterToolUse] = field(default_factory=list)
    stop_reason: str | None = None
    tokens_input: int = 0
    tokens_output: int = 0
    tokens_reasoning: int = 0
    tokens_cache_read: int = 0
    tokens_cache_creation: int = 0
    cost_usd: float = 0.0
    provider_metadata: dict[str, Any] = field(default_factory=dict)


class AgentAdapter(ABC):
    """Contract for invoking an agent and getting back a structured response."""

    agent: Agent | None
    """Optional handle to the Agent record this adapter was constructed for.
    Used by the Executor to bake snapshot ids into traces."""

    model: ModelRef | None = None
    """Optional model the agent runs, when known. Embedded/cli/http agents are
    black boxes; a cli/http spec may declare `agent.model: {provider, name}` so
    the Executor can stamp the real model on the trace and price reported tokens.
    None when undeclared (the model stays "unknown" and cost comes only from a
    `cost_usd` the agent reports itself)."""

    @abstractmethod
    async def invoke(self, request: AdapterRequest) -> AdapterResponse: ...


class EmbeddedAdapter(AgentAdapter):
    """Wrap a Python callable as an AgentAdapter.

    Useful for:
    - in-repo agents written as functions.
    - tests where we want deterministic responses.
    """

    def __init__(self, fn: EmbeddedCallable, *, agent: Agent | None = None) -> None:
        if not callable(fn):
            raise TypeError("EmbeddedAdapter requires a callable")
        self._fn = fn
        self.agent = agent

    async def invoke(self, request: AdapterRequest) -> AdapterResponse:
        try:
            if inspect.iscoroutinefunction(self._fn):
                result = await self._fn(request)
            else:
                result = await asyncio.to_thread(self._fn, request)
                if inspect.isawaitable(result):
                    # A sync callable that itself returned a coroutine (e.g. a
                    # lambda wrapping an async fn). Await it off the thread.
                    result = await result
        except Exception as exc:
            raise AdapterError(f"embedded callable raised: {exc}") from exc
        if not isinstance(result, AdapterResponse):
            hint = ""
            if inspect.isawaitable(result):
                hint = (
                    " — did you forget to await an async call? An `async def` entrypoint "
                    "should return its value directly; selfevals awaits it natively."
                )
            raise AdapterError(
                f"embedded callable returned {type(result).__name__}, "
                f"expected AdapterResponse{hint}"
            )
        return result


class CliCommandAdapter(AgentAdapter):
    """Run a subprocess with JSON-over-stdio.

    Protocol:
      stdin  ← JSON-encoded AdapterRequest
      stdout → JSON-encoded AdapterResponse
      non-zero exit code → AdapterError

    The subprocess is spawned and awaited asynchronously, so many cases
    can run concurrently without blocking the event loop.
    """

    def __init__(
        self,
        command: list[str],
        *,
        env: dict[str, str] | None = None,
        timeout_seconds: float | None = 60.0,
        agent: Agent | None = None,
        model: ModelRef | None = None,
    ) -> None:
        if not command:
            raise ValueError("command must be non-empty")
        self._command = list(command)
        self._env = env
        self._timeout = timeout_seconds
        self.agent = agent
        self.model = model

    async def invoke(self, request: AdapterRequest) -> AdapterResponse:
        payload = json.dumps(_request_to_json(request)).encode("utf-8")
        proc = await asyncio.create_subprocess_exec(
            *self._command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=payload), timeout=self._timeout
            )
        except TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise AdapterError(f"command timed out after {self._timeout}s") from exc
        if proc.returncode != 0:
            raise AdapterError(
                f"command exited with {proc.returncode}: "
                f"stderr={(stderr or b'').decode('utf-8', errors='replace')[:1000]}"
            )
        try:
            data = json.loads((stdout or b"").decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AdapterError(f"could not decode subprocess stdout as JSON: {exc}") from exc
        return _json_to_response(data)


class HttpEndpointAdapter(AgentAdapter):
    """POST JSON to a URL, parse JSON response.

    Protocol:
      POST {url}
      body  ← JSON-encoded AdapterRequest
      response → JSON-encoded AdapterResponse
      non-2xx → AdapterError

    Backed by `httpx.AsyncClient`, so the POST is awaited natively and
    many endpoints can be hit concurrently.
    """

    def __init__(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout_seconds: float = 60.0,
        agent: Agent | None = None,
        model: ModelRef | None = None,
    ) -> None:
        if not url:
            raise ValueError("url must be non-empty")
        self._url = url
        self._headers = {"Content-Type": "application/json", **(headers or {})}
        self._timeout = timeout_seconds
        self.agent = agent
        self.model = model

    async def invoke(self, request: AdapterRequest) -> AdapterResponse:
        payload = _request_to_json(request)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(self._url, json=payload, headers=self._headers)
                resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise AdapterError(
                f"HTTP adapter got {exc.response.status_code} "
                f"{exc.response.reason_phrase} from {self._url}"
            ) from exc
        except httpx.TimeoutException as exc:
            raise AdapterError(
                f"HTTP adapter timed out after {self._timeout}s on {self._url}"
            ) from exc
        except httpx.HTTPError as exc:
            # Transport-level failure (connection refused, DNS, etc.). Include
            # the URL so the message is actionable.
            raise AdapterError(
                f"HTTP adapter could not reach {self._url} ({exc}); "
                f"check the endpoint is running and reachable from this host"
            ) from exc
        try:
            data = json.loads(resp.content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AdapterError(
                f"HTTP adapter could not decode response from {self._url} as JSON: {exc}"
            ) from exc
        return _json_to_response(data)


def _request_to_json(req: AdapterRequest) -> dict[str, Any]:
    return {
        "workspace_id": req.workspace_id,
        "case_id": req.case_id,
        "input": req.input,
        "context": req.context,
        "tools_allowed": list(req.tools_allowed),
        "parameters": dict(req.parameters),
        "metadata": dict(req.metadata),
    }


def _json_to_response(data: dict[str, Any]) -> AdapterResponse:
    if not isinstance(data, dict):
        raise AdapterError(f"adapter response must be a JSON object, got {type(data).__name__}")
    tool_uses_raw = data.get("tool_uses", []) or []
    tool_uses: list[AdapterToolUse] = []
    for tu in tool_uses_raw:
        if not isinstance(tu, dict):
            raise AdapterError("tool_uses entries must be objects")
        tool_uses.append(
            AdapterToolUse(
                tool=str(tu.get("tool", "")),
                tool_use_id=str(tu.get("tool_use_id", "")),
                args=dict(tu.get("args") or {}),
            )
        )
    return AdapterResponse(
        content=data.get("content"),
        structured_output=data.get("structured_output"),
        tool_uses=tool_uses,
        stop_reason=data.get("stop_reason"),
        tokens_input=int(data.get("tokens_input", 0) or 0),
        tokens_output=int(data.get("tokens_output", 0) or 0),
        tokens_reasoning=int(data.get("tokens_reasoning", 0) or 0),
        tokens_cache_read=int(data.get("tokens_cache_read", 0) or 0),
        tokens_cache_creation=int(data.get("tokens_cache_creation", 0) or 0),
        cost_usd=float(data.get("cost_usd", 0.0) or 0.0),
        provider_metadata=dict(data.get("provider_metadata") or {}),
    )
