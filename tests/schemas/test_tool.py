from __future__ import annotations

import pytest
from pydantic import ValidationError

from bootstrap.schemas.enums import ToolStatus
from bootstrap.schemas.tool import Tool


def _make(**overrides: object) -> Tool:
    base: dict[str, object] = {
        "id": Tool.make_id(),
        "workspace_id": "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ",
        "name": "search_policy_docs",
        "description": "Vector search over policy corpus.",
    }
    base.update(overrides)
    return Tool(**base)  # type: ignore[arg-type]


def test_tool_defaults() -> None:
    tool = _make()
    assert tool.status == ToolStatus.DRAFT
    assert tool.side_effects is False
    assert tool.code_pointer is None


def test_tool_schema_alias() -> None:
    tool = _make()
    payload = tool.model_dump(by_alias=True)
    assert "schema" in payload
    assert "schema_" not in payload


def test_tool_requires_name_and_description() -> None:
    with pytest.raises(ValidationError):
        _make(name="")
    with pytest.raises(ValidationError):
        _make(description="")


def test_tool_status_transitions_via_assignment() -> None:
    tool = _make()
    tool.status = ToolStatus.ACTIVE
    assert tool.status == ToolStatus.ACTIVE
    with pytest.raises(ValidationError):
        tool.status = "deployed"  # type: ignore[assignment]
