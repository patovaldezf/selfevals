"""Tool: first-class entity for tools an Agent can invoke.

A Tool is what `editable.tool_code` and `editable.tool_descriptions` toggle
in an Experiment. The `code_pointer` references the stored implementation
(a path under the object store); `content_hash` pins identity for replay.
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import Field

from bootstrap.schemas._base import BaseEntity, BootstrapModel, NonEmptyStr
from bootstrap.schemas.enums import ToolStatus


class ToolSchema(BootstrapModel):
    """JSON-Schema-shaped definition of a tool's input arguments.

    Kept as opaque dicts for MVP — runtime validators consume this.
    """

    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] | None = None


class Tool(BaseEntity):
    _id_prefix: ClassVar[str] = "tl"

    name: NonEmptyStr
    description: NonEmptyStr
    schema_: ToolSchema = Field(default_factory=ToolSchema, alias="schema")
    code_pointer: str | None = None
    """Pointer (e.g. `oss://workspace/tools/{id}/v{n}/impl.py`) or None for
    declarative/external tools whose code lives outside bootstrap."""

    side_effects: bool = False
    """Whether invoking this tool can mutate external state. Influences
    sandbox routing — `side_effects=true` tools are mocked under `dry_run`."""

    content_hash: str | None = None
    status: ToolStatus = ToolStatus.DRAFT
