from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from selfevals.runner.adapters import (
    AdapterError,
    AdapterRequest,
    AdapterResponse,
    AdapterToolUse,
    CliCommandAdapter,
    EmbeddedAdapter,
    HttpEndpointAdapter,
)

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _req() -> AdapterRequest:
    return AdapterRequest(
        workspace_id=WS,
        case_id="ec_x",
        input={"messages": [{"role": "user", "content": "hi"}]},
    )


def test_get_model_param_returns_value_from_envelope() -> None:
    req = AdapterRequest(
        workspace_id=WS,
        case_id="ec_x",
        input={},
        parameters={"model_params": {"level": 0.7}},
    )
    assert req.get_model_param("level", 0.0) == 0.7


def test_get_model_param_returns_default_for_absent_key() -> None:
    req = AdapterRequest(
        workspace_id=WS,
        case_id="ec_x",
        input={},
        parameters={"model_params": {"level": 0.7}},
    )
    assert req.get_model_param("missing", 0.0) == 0.0


def test_get_model_param_returns_default_without_envelope() -> None:
    req = AdapterRequest(
        workspace_id=WS,
        case_id="ec_x",
        input={},
        parameters={"temperature": 0.2},
    )
    assert req.get_model_param("level", 0.0) == 0.0


@pytest.mark.asyncio
async def test_embedded_invokes_sync_callable() -> None:
    def fn(req: AdapterRequest) -> AdapterResponse:
        return AdapterResponse(content="hello", tokens_input=5, tokens_output=2)

    adapter = EmbeddedAdapter(fn)
    resp = await adapter.invoke(_req())
    assert resp.content == "hello"
    assert resp.tokens_input == 5


@pytest.mark.asyncio
async def test_embedded_invokes_async_callable() -> None:
    async def fn(req: AdapterRequest) -> AdapterResponse:
        return AdapterResponse(content="async-hello", tokens_input=7)

    adapter = EmbeddedAdapter(fn)
    resp = await adapter.invoke(_req())
    assert resp.content == "async-hello"
    assert resp.tokens_input == 7


@pytest.mark.asyncio
async def test_embedded_sync_callable_runs_off_event_loop() -> None:
    # A blocking sync callable must be bridged via to_thread so it does not
    # stall the event loop. We can't easily assert the thread here, but we can
    # confirm a callable doing thread-only work (e.g. accessing the running
    # loop would fail) still succeeds.
    import asyncio

    def fn(req: AdapterRequest) -> AdapterResponse:
        # There is no running loop on a worker thread.
        with pytest.raises(RuntimeError):
            asyncio.get_running_loop()
        return AdapterResponse(content="threaded")

    resp = await EmbeddedAdapter(fn).invoke(_req())
    assert resp.content == "threaded"


def test_embedded_rejects_non_callable() -> None:
    with pytest.raises(TypeError):
        EmbeddedAdapter("not a callable")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_embedded_wraps_exceptions_as_adapter_error() -> None:
    def fn(_: AdapterRequest) -> AdapterResponse:
        raise ZeroDivisionError("boom")

    with pytest.raises(AdapterError, match="boom"):
        await EmbeddedAdapter(fn).invoke(_req())


@pytest.mark.asyncio
async def test_embedded_wraps_async_exceptions_as_adapter_error() -> None:
    async def fn(_: AdapterRequest) -> AdapterResponse:
        raise ZeroDivisionError("boom-async")

    with pytest.raises(AdapterError, match="boom-async"):
        await EmbeddedAdapter(fn).invoke(_req())


@pytest.mark.asyncio
async def test_embedded_rejects_wrong_return_type() -> None:
    def fn(_: AdapterRequest) -> AdapterResponse:
        return {"content": "wrong"}  # type: ignore[return-value]

    with pytest.raises(AdapterError, match="expected AdapterResponse"):
        await EmbeddedAdapter(fn).invoke(_req())


_ECHO_SCRIPT = """
import json, sys
req = json.loads(sys.stdin.read())
resp = {
    "content": "echo: " + req["case_id"],
    "tokens_input": 10,
    "tokens_output": 4,
    "stop_reason": "end_turn",
    "tool_uses": [{"tool": "search", "tool_use_id": "toolu_01"}],
}
sys.stdout.write(json.dumps(resp))
"""


@pytest.mark.asyncio
async def test_cli_adapter_roundtrip(tmp_path: Path) -> None:
    script = tmp_path / "agent.py"
    script.write_text(_ECHO_SCRIPT)
    adapter = CliCommandAdapter([sys.executable, str(script)])
    resp = await adapter.invoke(_req())
    assert resp.content == "echo: ec_x"
    assert resp.tokens_input == 10
    assert resp.stop_reason == "end_turn"
    assert resp.tool_uses == [AdapterToolUse(tool="search", tool_use_id="toolu_01")]


@pytest.mark.asyncio
async def test_cli_adapter_propagates_nonzero_exit(tmp_path: Path) -> None:
    script = tmp_path / "agent_fail.py"
    script.write_text("import sys; sys.stderr.write('crash'); sys.exit(2)")
    adapter = CliCommandAdapter([sys.executable, str(script)])
    with pytest.raises(AdapterError, match="exited with 2"):
        await adapter.invoke(_req())


@pytest.mark.asyncio
async def test_cli_adapter_rejects_invalid_json(tmp_path: Path) -> None:
    script = tmp_path / "agent_bad.py"
    script.write_text("import sys; sys.stdout.write('not json')")
    adapter = CliCommandAdapter([sys.executable, str(script)])
    with pytest.raises(AdapterError, match="JSON"):
        await adapter.invoke(_req())


@pytest.mark.asyncio
async def test_cli_adapter_times_out(tmp_path: Path) -> None:
    script = tmp_path / "agent_slow.py"
    script.write_text("import time; time.sleep(5)")
    adapter = CliCommandAdapter([sys.executable, str(script)], timeout_seconds=0.3)
    with pytest.raises(AdapterError, match="timed out"):
        await adapter.invoke(_req())


def test_cli_adapter_requires_command() -> None:
    with pytest.raises(ValueError):
        CliCommandAdapter([])


@pytest.mark.asyncio
async def test_http_adapter_roundtrip_mock_transport() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "content": "served: " + body["case_id"],
                "tokens_input": 3,
                "tokens_output": 1,
            },
        )

    adapter = HttpEndpointAdapter("http://test.local/agent")
    # Inject a mock transport by monkeypatching the client factory.
    import selfevals.runner.adapters as adapters_mod

    real_client = adapters_mod.httpx.AsyncClient

    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("timeout", None)
        return real_client(transport=httpx.MockTransport(handler))

    adapters_mod.httpx.AsyncClient = factory  # type: ignore[assignment]
    try:
        resp = await adapter.invoke(_req())
    finally:
        adapters_mod.httpx.AsyncClient = real_client  # type: ignore[assignment]
    assert resp.content == "served: ec_x"
    assert resp.tokens_input == 3


@pytest.mark.asyncio
async def test_http_adapter_maps_status_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    adapter = HttpEndpointAdapter("http://test.local/agent")
    import selfevals.runner.adapters as adapters_mod

    real_client = adapters_mod.httpx.AsyncClient

    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("timeout", None)
        return real_client(transport=httpx.MockTransport(handler))

    adapters_mod.httpx.AsyncClient = factory  # type: ignore[assignment]
    try:
        with pytest.raises(AdapterError, match="503"):
            await adapter.invoke(_req())
    finally:
        adapters_mod.httpx.AsyncClient = real_client  # type: ignore[assignment]


def test_http_adapter_rejects_empty_url() -> None:
    with pytest.raises(ValueError):
        HttpEndpointAdapter("")


@pytest.mark.asyncio
async def test_http_adapter_handles_transport_error() -> None:
    # 127.0.0.1 with a port we did not open should fail fast.
    adapter = HttpEndpointAdapter("http://127.0.0.1:1/", timeout_seconds=1.0)
    with pytest.raises(AdapterError, match="could not reach"):
        await adapter.invoke(_req())


# --- F6: error classification (transient vs permanent) ------------------------


async def _http_invoke_expecting_error(
    handler: Callable[[httpx.Request], httpx.Response],
) -> AdapterError:
    """Run HttpEndpointAdapter against a mock handler, return the AdapterError."""
    adapter = HttpEndpointAdapter("http://test.local/agent")
    import selfevals.runner.adapters as adapters_mod

    real_client = adapters_mod.httpx.AsyncClient

    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("timeout", None)
        return real_client(transport=httpx.MockTransport(handler))

    adapters_mod.httpx.AsyncClient = factory  # type: ignore[assignment]
    try:
        with pytest.raises(AdapterError) as excinfo:
            await adapter.invoke(_req())
    finally:
        adapters_mod.httpx.AsyncClient = real_client  # type: ignore[assignment]
    return excinfo.value


@pytest.mark.asyncio
async def test_http_429_is_retryable_with_retry_after() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="slow down", headers={"Retry-After": "7"})

    err = await _http_invoke_expecting_error(handler)
    assert err.retryable is True
    assert err.status_code == 429
    assert err.retry_after_seconds == 7.0


@pytest.mark.asyncio
async def test_http_503_is_retryable() -> None:
    err = await _http_invoke_expecting_error(lambda r: httpx.Response(503, text="down"))
    assert err.retryable is True
    assert err.status_code == 503


@pytest.mark.asyncio
async def test_http_400_is_permanent() -> None:
    err = await _http_invoke_expecting_error(lambda r: httpx.Response(400, text="bad request"))
    assert err.retryable is False
    assert err.status_code == 400


@pytest.mark.asyncio
async def test_http_timeout_is_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    err = await _http_invoke_expecting_error(handler)
    assert err.retryable is True


@pytest.mark.asyncio
async def test_embedded_classifies_rate_limit_as_retryable() -> None:
    class _FakeRateLimitError(Exception):
        status_code = 429
        retry_after = 3

    def fn(req: AdapterRequest) -> AdapterResponse:
        raise _FakeRateLimitError("rate limited")

    adapter = EmbeddedAdapter(fn)
    with pytest.raises(AdapterError) as excinfo:
        await adapter.invoke(_req())
    assert excinfo.value.retryable is True
    assert excinfo.value.status_code == 429
    assert excinfo.value.retry_after_seconds == 3.0


@pytest.mark.asyncio
async def test_embedded_plain_exception_is_permanent() -> None:
    def fn(req: AdapterRequest) -> AdapterResponse:
        raise ValueError("logic bug")

    adapter = EmbeddedAdapter(fn)
    with pytest.raises(AdapterError) as excinfo:
        await adapter.invoke(_req())
    assert excinfo.value.retryable is False


@pytest.mark.asyncio
async def test_cli_timeout_is_retryable(tmp_path: Path) -> None:
    script = tmp_path / "slow.py"
    script.write_text("import time; time.sleep(5)")
    adapter = CliCommandAdapter([sys.executable, str(script)], timeout_seconds=0.2)
    with pytest.raises(AdapterError) as excinfo:
        await adapter.invoke(_req())
    assert excinfo.value.retryable is True


@pytest.mark.asyncio
async def test_cli_nonzero_exit_is_permanent(tmp_path: Path) -> None:
    script = tmp_path / "crash.py"
    script.write_text("import sys; sys.exit(2)")
    adapter = CliCommandAdapter([sys.executable, str(script)])
    with pytest.raises(AdapterError) as excinfo:
        await adapter.invoke(_req())
    assert excinfo.value.retryable is False
