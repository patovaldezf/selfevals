"""m0010 — inline tool args/result on trace_tool_calls.

Trace schema 1.4.0 adds `ToolCallSpan.{args_inline,result_inline}` — the small-
payload inline mirror of the existing `*_pointer`/`*_hash` columns, matching the
inline trio LLM spans already carry (schema 1.3.0). Adapters can now report tool
results (`AdapterToolUse.result`), so a tool call shows its output, not just its
input, without an object-store fetch. Additive columns; existing rows read back
as NULL (no inline), which the mapper handles as "not inlined".
"""

from __future__ import annotations

from typing import Any

_SQL = """
ALTER TABLE trace_tool_calls ADD COLUMN IF NOT EXISTS args_inline   TEXT;
ALTER TABLE trace_tool_calls ADD COLUMN IF NOT EXISTS result_inline TEXT;
"""


def up(cur: Any) -> None:
    cur.execute(_SQL)
