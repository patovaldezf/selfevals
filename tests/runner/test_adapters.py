from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from bootstrap.runner.adapters import (
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


def test_embedded_invokes_callable() -> None:
    def fn(req: AdapterRequest) -> AdapterResponse:
        return AdapterResponse(content="hello", tokens_input=5, tokens_output=2)

    adapter = EmbeddedAdapter(fn)
    resp = adapter.invoke(_req())
    assert resp.content == "hello"
    assert resp.tokens_input == 5


def test_embedded_rejects_non_callable() -> None:
    with pytest.raises(TypeError):
        EmbeddedAdapter("not a callable")  # type: ignore[arg-type]


def test_embedded_wraps_exceptions_as_adapter_error() -> None:
    def fn(_: AdapterRequest) -> AdapterResponse:
        raise ZeroDivisionError("boom")

    with pytest.raises(AdapterError, match="boom"):
        EmbeddedAdapter(fn).invoke(_req())


def test_embedded_rejects_wrong_return_type() -> None:
    def fn(_: AdapterRequest) -> AdapterResponse:
        return {"content": "wrong"}  # type: ignore[return-value]

    with pytest.raises(AdapterError, match="expected AdapterResponse"):
        EmbeddedAdapter(fn).invoke(_req())


# --- CliCommandAdapter ---

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


def test_cli_adapter_roundtrip(tmp_path: Path) -> None:
    script = tmp_path / "agent.py"
    script.write_text(_ECHO_SCRIPT)
    adapter = CliCommandAdapter([sys.executable, str(script)])
    resp = adapter.invoke(_req())
    assert resp.content == "echo: ec_x"
    assert resp.tokens_input == 10
    assert resp.stop_reason == "end_turn"
    assert resp.tool_uses == [AdapterToolUse(tool="search", tool_use_id="toolu_01")]


def test_cli_adapter_propagates_nonzero_exit(tmp_path: Path) -> None:
    script = tmp_path / "agent_fail.py"
    script.write_text("import sys; sys.stderr.write('crash'); sys.exit(2)")
    adapter = CliCommandAdapter([sys.executable, str(script)])
    with pytest.raises(AdapterError, match="exited with 2"):
        adapter.invoke(_req())


def test_cli_adapter_rejects_invalid_json(tmp_path: Path) -> None:
    script = tmp_path / "agent_bad.py"
    script.write_text("import sys; sys.stdout.write('not json')")
    adapter = CliCommandAdapter([sys.executable, str(script)])
    with pytest.raises(AdapterError, match="JSON"):
        adapter.invoke(_req())


def test_cli_adapter_requires_command() -> None:
    with pytest.raises(ValueError):
        CliCommandAdapter([])


# --- HttpEndpointAdapter (use a stdlib loopback HTTPServer) ---


def test_http_adapter_roundtrip(tmp_path: Path) -> None:
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers["Content-Length"])
            body = json.loads(self.rfile.read(length).decode())
            resp_body = json.dumps(
                {
                    "content": "served: " + body["case_id"],
                    "tokens_input": 3,
                    "tokens_output": 1,
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_body)))
            self.end_headers()
            self.wfile.write(resp_body)

        def log_message(self, *args: object, **kwargs: object) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        adapter = HttpEndpointAdapter(f"http://127.0.0.1:{port}/")
        resp = adapter.invoke(_req())
        assert resp.content == "served: ec_x"
        assert resp.tokens_input == 3
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_adapter_rejects_empty_url() -> None:
    with pytest.raises(ValueError):
        HttpEndpointAdapter("")


def test_http_adapter_handles_transport_error() -> None:
    # 127.0.0.1 with a port we did not open should fail fast.
    adapter = HttpEndpointAdapter("http://127.0.0.1:1/", timeout_seconds=1.0)
    with pytest.raises(AdapterError):
        adapter.invoke(_req())
