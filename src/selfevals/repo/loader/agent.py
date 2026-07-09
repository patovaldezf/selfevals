"""Parse and serialize the `agent:` block into a transport-tagged `AgentSpec`."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from selfevals.repo.loader.models import (
    AgentEntrypoint,
    AgentModelDecl,
    AgentSpec,
    CliAgentSpec,
    EmbeddedAgentSpec,
    HttpAgentSpec,
    LoaderError,
)

_SUPPORTED_AGENT_TYPES = {"embedded", "cli", "http"}


def resolve_agent_callable(entrypoint: AgentEntrypoint) -> Any:
    """Import `entrypoint.module` and return the named attribute.

    Kept separate from spec loading so callers can validate a spec
    without triggering user-code import side effects.
    """
    try:
        module = importlib.import_module(entrypoint.module)
    except ImportError as exc:
        raise LoaderError(
            f"agent entrypoint module {entrypoint.module!r} could not be imported: {exc}"
        ) from exc
    try:
        return getattr(module, entrypoint.attribute)
    except AttributeError as exc:
        raise LoaderError(
            f"agent entrypoint {entrypoint.raw!r}: "
            f"module {entrypoint.module!r} has no attribute {entrypoint.attribute!r}"
        ) from exc


def build_agent_spec(spec_path: Path, raw: dict[str, Any]) -> AgentSpec:
    """Parse the `agent:` block into a transport-tagged spec.

    Accepts the legacy `{entrypoint: ...}` shape (embedded) and the tagged
    `{type: embedded|cli|http, ...}` shape. The type tag selects which
    adapter the CLI wires up; required fields are validated per type so a
    typo surfaces here instead of at adapter-construction time.
    """
    agent_section = raw.get("agent", {})
    if not isinstance(agent_section, dict):
        raise LoaderError(f"{spec_path}: `agent:` must be a mapping")

    type_ = agent_section.get("type")
    if type_ is None:
        # Legacy shape: `agent: {entrypoint: "mod:fn"}` → embedded.
        return EmbeddedAgentSpec(entrypoint=_parse_entrypoint(spec_path, agent_section))

    if not isinstance(type_, str) or type_ not in _SUPPORTED_AGENT_TYPES:
        raise LoaderError(
            f"{spec_path}: agent.type must be one of "
            f"{sorted(_SUPPORTED_AGENT_TYPES)}; got {type_!r}"
        )

    if type_ == "embedded":
        return EmbeddedAgentSpec(entrypoint=_parse_entrypoint(spec_path, agent_section))
    if type_ == "cli":
        return _build_cli_agent_spec(spec_path, agent_section)
    return _build_http_agent_spec(spec_path, agent_section)


def _parse_entrypoint(spec_path: Path, agent_section: dict[str, Any]) -> AgentEntrypoint:
    entrypoint = agent_section.get("entrypoint")
    if not isinstance(entrypoint, str) or ":" not in entrypoint:
        raise LoaderError(
            f"{spec_path}: agent.entrypoint must be a string of the form "
            f"'package.module:callable_name'; got {entrypoint!r}"
        )
    module, _, attribute = entrypoint.partition(":")
    if not module or not attribute:
        raise LoaderError(
            f"{spec_path}: agent.entrypoint {entrypoint!r} is missing module or callable name"
        )
    return AgentEntrypoint(raw=entrypoint, module=module, attribute=attribute)


def _build_cli_agent_spec(spec_path: Path, agent_section: dict[str, Any]) -> CliAgentSpec:
    if "entrypoint" in agent_section:
        raise LoaderError(
            f"{spec_path}: agent.type 'cli' does not take an `entrypoint`; use `command:` instead"
        )
    command = agent_section.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(c, str) for c in command):
        raise LoaderError(
            f"{spec_path}: agent.type 'cli' requires `command:` as a non-empty list of strings; "
            f"got {command!r}"
        )
    env = _parse_str_map(spec_path, agent_section.get("env"), field_name="agent.env")
    timeout = _parse_timeout(spec_path, agent_section.get("timeout_seconds"))
    model = _parse_agent_model(spec_path, agent_section.get("model"))
    cwd = agent_section.get("cwd")
    if cwd is not None and not isinstance(cwd, str):
        raise LoaderError(f"{spec_path}: agent.cwd must be a string; got {cwd!r}")
    return CliAgentSpec(
        command=[str(c) for c in command],
        env=env,
        timeout_seconds=timeout,
        model=model,
        cwd=cwd,
    )


def _build_http_agent_spec(spec_path: Path, agent_section: dict[str, Any]) -> HttpAgentSpec:
    if "entrypoint" in agent_section:
        raise LoaderError(
            f"{spec_path}: agent.type 'http' does not take an `entrypoint`; use `url:` instead"
        )
    url = agent_section.get("url")
    if not isinstance(url, str) or not url.strip():
        raise LoaderError(
            f"{spec_path}: agent.type 'http' requires `url:` as a non-empty string; got {url!r}"
        )
    headers = _parse_str_map(spec_path, agent_section.get("headers"), field_name="agent.headers")
    timeout = _parse_timeout(spec_path, agent_section.get("timeout_seconds"))
    model = _parse_agent_model(spec_path, agent_section.get("model"))
    return HttpAgentSpec(url=url, headers=headers, timeout_seconds=timeout, model=model)


def _parse_agent_model(spec_path: Path, value: Any) -> AgentModelDecl | None:
    """Parse the optional `agent.model: {provider, name}` block."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise LoaderError(
            f"{spec_path}: agent.model must be a mapping with `provider` and `name`; got {value!r}"
        )
    provider = value.get("provider")
    name = value.get("name")
    if not isinstance(provider, str) or not provider.strip():
        raise LoaderError(f"{spec_path}: agent.model.provider must be a non-empty string")
    if not isinstance(name, str) or not name.strip():
        raise LoaderError(f"{spec_path}: agent.model.name must be a non-empty string")
    return AgentModelDecl(provider=provider, name=name)


def _parse_str_map(spec_path: Path, value: Any, *, field_name: str) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        raise LoaderError(f"{spec_path}: {field_name} must be a mapping of string→string")
    return {k: v for k, v in value.items()}


def _parse_timeout(spec_path: Path, value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise LoaderError(
            f"{spec_path}: agent.timeout_seconds must be a positive number; got {value!r}"
        )
    return float(value)


def dump_entrypoint(entrypoint: AgentEntrypoint) -> str:
    return entrypoint.raw


def dump_agent_model(model: AgentModelDecl | None) -> dict[str, str] | None:
    if model is None:
        return None
    return {"provider": model.provider, "name": model.name}


def dump_agent_spec(agent: AgentSpec) -> dict[str, Any]:
    if isinstance(agent, EmbeddedAgentSpec):
        return {"type": "embedded", "entrypoint": dump_entrypoint(agent.entrypoint)}
    if isinstance(agent, CliAgentSpec):
        payload: dict[str, Any] = {"type": "cli", "command": list(agent.command)}
        if agent.env is not None:
            payload["env"] = dict(agent.env)
        if agent.timeout_seconds is not None:
            payload["timeout_seconds"] = agent.timeout_seconds
        if agent.model is not None:
            payload["model"] = dump_agent_model(agent.model)
        if agent.cwd is not None:
            payload["cwd"] = agent.cwd
        return payload
    payload = {"type": "http", "url": agent.url}
    if agent.headers is not None:
        payload["headers"] = dict(agent.headers)
    if agent.timeout_seconds is not None:
        payload["timeout_seconds"] = agent.timeout_seconds
    if agent.model is not None:
        payload["model"] = dump_agent_model(agent.model)
    return payload
