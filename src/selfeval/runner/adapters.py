"""Agent adapters: how selfeval invokes the agent under test.

`AgentAdapter` is the narrowest contract: given an `AdapterRequest` (the
case input and a frozen set of allowed tools), return an `AdapterResponse`
with the agent's reply plus any tool calls it made and token accounting.

The recorder ingests these into a Trace separately — adapters do not own
Trace assembly. This keeps adapters small and lets us swap them out for
mocks in tests.

Three concrete adapters ship in MVP:

- `EmbeddedAdapter` — wraps a plain Python callable. Useful for tests
  and for fast-iteration in-repo agents.
- `CliCommandAdapter` — runs a subprocess with JSON-over-stdio.
- `HttpEndpointAdapter` — POSTs JSON to a URL (e.g. an ngrok tunnel).

CLI and HTTP adapters are intentionally minimal here — they ship as
stubs with the protocol nailed down. Full hardening (retries, streaming,
auth headers) lands in a later PR when we dogfood Seals end-to-end.
"""

from __future__ import annotations

import json
import subprocess
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

if TYPE_CHECKING:
    from selfeval.schemas.fleet import Agent

EmbeddedCallable = Callable[["AdapterRequest"], "AdapterResponse"]


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

    @abstractmethod
    def invoke(self, request: AdapterRequest) -> AdapterResponse: ...


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

    def invoke(self, request: AdapterRequest) -> AdapterResponse:
        try:
            result = self._fn(request)
        except Exception as exc:
            raise AdapterError(f"embedded callable raised: {exc}") from exc
        if not isinstance(result, AdapterResponse):
            raise AdapterError(
                f"embedded callable returned {type(result).__name__}, expected AdapterResponse"
            )
        return result


class CliCommandAdapter(AgentAdapter):
    """Run a subprocess with JSON-over-stdio.

    Protocol:
      stdin  ← JSON-encoded AdapterRequest
      stdout → JSON-encoded AdapterResponse
      non-zero exit code → AdapterError

    This is intentionally minimal for MVP. Streaming, retries, and richer
    auth headers land later.
    """

    def __init__(
        self,
        command: list[str],
        *,
        env: dict[str, str] | None = None,
        timeout_seconds: float | None = 60.0,
        agent: Agent | None = None,
    ) -> None:
        if not command:
            raise ValueError("command must be non-empty")
        self._command = list(command)
        self._env = env
        self._timeout = timeout_seconds
        self.agent = agent

    def invoke(self, request: AdapterRequest) -> AdapterResponse:
        payload = json.dumps(_request_to_json(request)).encode("utf-8")
        try:
            proc = subprocess.run(
                self._command,
                input=payload,
                capture_output=True,
                timeout=self._timeout,
                env=self._env,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AdapterError(f"command timed out after {self._timeout}s: {exc}") from exc
        if proc.returncode != 0:
            raise AdapterError(
                f"command exited with {proc.returncode}: "
                f"stderr={(proc.stderr or b'').decode('utf-8', errors='replace')[:1000]}"
            )
        try:
            data = json.loads(proc.stdout.decode("utf-8"))
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

    For MVP this uses stdlib `urllib` — no third-party HTTP dep.
    """

    def __init__(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout_seconds: float = 60.0,
        agent: Agent | None = None,
    ) -> None:
        if not url:
            raise ValueError("url must be non-empty")
        self._url = url
        self._headers = {"Content-Type": "application/json", **(headers or {})}
        self._timeout = timeout_seconds
        self.agent = agent

    def invoke(self, request: AdapterRequest) -> AdapterResponse:
        body = json.dumps(_request_to_json(request)).encode("utf-8")
        req = Request(self._url, data=body, headers=self._headers, method="POST")
        try:
            with urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read()
        except HTTPError as exc:
            raise AdapterError(
                f"HTTP adapter got {exc.code} {exc.reason} from {self._url}"
            ) from exc
        except URLError as exc:
            # `URLError.reason` is usually a `socket.timeout` or an OSError; both
            # render fine via str(). Include the URL so the message is actionable.
            raise AdapterError(
                f"HTTP adapter could not reach {self._url} ({exc.reason}); "
                f"check the endpoint is running and reachable from this host"
            ) from exc
        except TimeoutError as exc:  # pragma: no cover - URLError covers most cases
            raise AdapterError(
                f"HTTP adapter timed out after {self._timeout}s on {self._url}"
            ) from exc
        try:
            data = json.loads(raw.decode("utf-8"))
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
