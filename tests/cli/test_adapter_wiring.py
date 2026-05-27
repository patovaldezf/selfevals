"""Wiring point: transport-tagged agent specs → concrete adapters.

The loader only parses the `agent:` block into a typed spec; the CLI
dispatches that spec to an adapter. These tests pin the dispatch (one
adapter per variant) and the judge-fallback rule (only embedded agents
expose an in-process callable to reuse as a judge).
"""

from __future__ import annotations

import pytest

from selfevals.cli.commands import (
    CommandError,
    _agent_entrypoint_for_judge,
    _build_adapter,
)
from selfevals.repo.loader import (
    AgentEntrypoint,
    CliAgentSpec,
    EmbeddedAgentSpec,
    HttpAgentSpec,
)
from selfevals.runner.adapters import (
    CliCommandAdapter,
    EmbeddedAdapter,
    HttpEndpointAdapter,
)


def test_build_adapter_embedded() -> None:
    ep = AgentEntrypoint(
        raw="selfevals.repo.loader:resolve_agent_callable",
        module="selfevals.repo.loader",
        attribute="resolve_agent_callable",
    )
    adapter = _build_adapter(EmbeddedAgentSpec(entrypoint=ep))
    assert isinstance(adapter, EmbeddedAdapter)


def test_build_adapter_embedded_bad_entrypoint_is_command_error() -> None:
    ep = AgentEntrypoint(raw="not.a.real.mod:x", module="not.a.real.mod", attribute="x")
    with pytest.raises(CommandError, match="could not be imported"):
        _build_adapter(EmbeddedAgentSpec(entrypoint=ep))


def test_build_adapter_cli() -> None:
    spec = CliAgentSpec(command=["./bin/agent"], env={"TOKEN": "x"}, timeout_seconds=30.0)
    adapter = _build_adapter(spec)
    assert isinstance(adapter, CliCommandAdapter)
    assert adapter._command == ["./bin/agent"]
    assert adapter._env == {"TOKEN": "x"}
    assert adapter._timeout == 30.0


def test_build_adapter_cli_default_timeout() -> None:
    adapter = _build_adapter(CliAgentSpec(command=["./agent"]))
    assert isinstance(adapter, CliCommandAdapter)
    # When the spec omits timeout_seconds, the adapter default applies.
    assert adapter._timeout == 60.0


def test_build_adapter_http() -> None:
    spec = HttpAgentSpec(
        url="https://agent.example.com/eval",
        headers={"Authorization": "Bearer x"},
        timeout_seconds=12.5,
    )
    adapter = _build_adapter(spec)
    assert isinstance(adapter, HttpEndpointAdapter)
    assert adapter._url == "https://agent.example.com/eval"
    assert adapter._headers["Authorization"] == "Bearer x"
    assert adapter._timeout == 12.5


def test_build_adapter_http_default_timeout() -> None:
    adapter = _build_adapter(HttpAgentSpec(url="https://x/eval"))
    assert isinstance(adapter, HttpEndpointAdapter)
    assert adapter._timeout == 60.0


def test_judge_fallback_returns_embedded_entrypoint() -> None:
    ep = AgentEntrypoint(raw="m:f", module="m", attribute="f")
    assert _agent_entrypoint_for_judge("rubric", EmbeddedAgentSpec(entrypoint=ep)) is ep


def test_judge_fallback_rejects_cli_agent() -> None:
    with pytest.raises(CommandError, match="not embedded"):
        _agent_entrypoint_for_judge("rubric", CliAgentSpec(command=["./a"]))


def test_judge_fallback_rejects_http_agent() -> None:
    with pytest.raises(CommandError, match="not embedded"):
        _agent_entrypoint_for_judge("rubric", HttpAgentSpec(url="https://x"))
