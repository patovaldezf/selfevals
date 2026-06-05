"""Canonical projection of a domain `Span` into the trimmed view shape.

This is the single source of truth for the `SpanSummary` wire shape —
`{id, parent_id, kind, name, started_at, duration_ms, detail}` — shared by
two callers that must never drift:

* `api.queries._span_summary` wraps the dict in the `SpanSummary` Pydantic
  model for the REST snapshot (`GET /traces/{id}`);
* `trace.recorder` emits the dict directly to a `SpanSink` for live SSE,
  so an in-progress span reaches the browser in the exact shape the
  snapshot uses (the FE merges live spans into the snapshot list by id).

It lives in `trace/` and returns a plain dict — never importing from
`api/` — so the capture pipeline stays unaware that FastAPI / the SSE
view models exist. `api/` depends on `trace/`, not the other way round.
"""

from __future__ import annotations

from typing import Any

# Kind-specific high-value fields copied into `detail` so the trace
# inspector renders without fetching the full payload. Kept here (not in
# queries.py) because the live path projects the same fields — a divergence
# would make a live span render differently from its persisted twin.
_DETAIL_KEYS: frozenset[str] = frozenset(
    {
        "provider",
        "model",
        "model_version_pinned",
        "params",
        "tokens",
        "cost_usd",
        "time_to_first_token_ms",
        "tokens_per_second",
        "cache_hit",
        "retries",
        "output",
        "reasoning",
        "provider_metadata",
        "tool_name",
        "tool_use_id",
        "status",
        "error",
        "retriever",
        "top_k_requested",
        "top_k_returned",
        "retrieved",
        "decision_type",
        "chosen",
        "alternatives_considered",
        "guardrail",
        "passed",
        "error_type",
        "message",
        "recoverable",
        "system_prompt_pointer",
        "system_prompt_hash",
        "system_prompt_inline",
        "messages_pointer",
        "messages_hash",
        "messages_inline",
        "tools_offered",
        "tools_offered_hash",
        "args_pointer",
        "args_hash",
        "result_pointer",
        "result_hash",
        "query_pointer",
        "query_hash",
        "values_pointer",
        "values_hash",
    }
)


def span_view(span: Any) -> dict[str, Any]:
    """Project any `Span` subclass into the JSON-safe view dict.

    Surfaces kind + name + parent + timing on every span and copies the
    kind-specific high-value fields into `detail`. The result is fully
    JSON-serializable (`model_dump(mode="json")`), ready to hand to either
    the SSE encoder or `SpanSummary(**view)`.
    """
    payload = span.model_dump(mode="json")
    detail = {key: value for key, value in payload.items() if key in _DETAIL_KEYS}
    return {
        "id": span.id,
        "parent_id": span.parent_id,
        "kind": str(span.kind),
        "name": span.name,
        "started_at": payload["started_at"],
        "duration_ms": span.duration_ms,
        "detail": detail,
    }
