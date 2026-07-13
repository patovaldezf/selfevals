"""Playground: replay one LLM span against a different provider/model.

The trace viewer's "run this again with another model" — LangSmith's Playground.
Given a persisted `LLMCallSpan`, we resolve exactly what the model saw (system
prompt + messages, from the span's inline copy or object-store pointer) and
re-issue that same prompt to a provider/model the user picks, returning the
alternate output side-by-side with the original.

Provider calls go through the optional SDK extras (`selfevals[anthropic]`,
`selfevals[openai]`), imported lazily and degraded with the standard "pip
install selfevals[<provider>]" error so a missing extra is actionable rather
than an ImportError. Tools are NOT executed on replay — this compares the
model's *decision* (text + which tools it would call), with no side effects.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, cast

from selfevals.api.queries._shared import resolve_trace
from selfevals.api.schemas.traces import SpanReplayRequest, SpanReplayResponse
from selfevals.runner.pricing import estimate_cost
from selfevals.schemas.trace import LLMCallSpan, TokenBreakdown, Trace
from selfevals.storage.filesystem import FilesystemObjectStore
from selfevals.storage.interface import StorageInterface


class SpanReplayError(ValueError):
    """Replay could not run (bad span, missing payload, provider failure)."""


def _resolve(
    object_store: FilesystemObjectStore,
    *,
    inline: str | None,
    pointer: str | None,
) -> str | None:
    """Resolve a payload's full text.

    The pointer is authoritative: when a payload is large it's offloaded to the
    object store AND a *truncated* inline preview is kept on the span (capped at
    INLINE_PAYLOAD_MAX_CHARS). Replaying needs the whole prompt, so a set pointer
    wins over the inline. Only when there's no pointer is the inline the full
    payload (small enough to not have been offloaded).
    """
    if pointer is not None:
        try:
            return object_store.get(pointer).decode("utf-8")
        except Exception as exc:  # audit:ignore[broad_exception_catches] — object-store/decode failure → domain error
            raise SpanReplayError(f"could not resolve payload pointer: {exc}") from exc
    return inline


def _messages_from_payload(raw: str | None) -> list[dict[str, Any]]:
    """Parse the span's messages payload into a plain message list."""
    if not raw:
        raise SpanReplayError("span has no messages payload to replay")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SpanReplayError(f"messages payload is not valid JSON: {exc}") from exc
    if isinstance(parsed, dict) and isinstance(parsed.get("messages"), list):
        parsed = parsed["messages"]
    if not isinstance(parsed, list):
        raise SpanReplayError("messages payload is not a list of messages")
    return [m for m in parsed if isinstance(m, dict)]


def _find_llm_span(trace: Trace, span_id: str) -> LLMCallSpan:
    for span in trace.spans:
        if span.id == span_id:
            if not isinstance(span, LLMCallSpan):
                raise SpanReplayError(f"span {span_id} is not an llm_call span")
            return span
    raise SpanReplayError(f"span {span_id} not found in trace")


# ── provider callers ─────────────────────────────────────────────────────────
# Return (content, input_tokens, output_tokens). Injectable so tests use a fake.


def _call_anthropic(
    *, model: str, system: str | None, messages: list[dict[str, Any]], params: dict[str, Any]
) -> tuple[str, int, int]:
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - depends on extras
        raise SpanReplayError(
            "replaying an Anthropic model needs the SDK — pip install selfevals[anthropic]"
        ) from exc
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SpanReplayError("ANTHROPIC_API_KEY is not set")
    client = anthropic.Anthropic()
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": int(params.get("max_tokens", 1024)),
        "messages": [m for m in messages if m.get("role") != "system"],
    }
    if system:
        kwargs["system"] = system
    if "temperature" in params:
        kwargs["temperature"] = params["temperature"]
    try:
        resp = client.messages.create(**kwargs)
    except Exception as exc:  # audit:ignore[broad_exception_catches] — wrap provider SDK error (network/auth/rate-limit)
        raise SpanReplayError(f"anthropic call failed: {exc}") from exc
    text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text")
    return text, resp.usage.input_tokens, resp.usage.output_tokens


def _call_openai(
    *, model: str, system: str | None, messages: list[dict[str, Any]], params: dict[str, Any]
) -> tuple[str, int, int]:
    try:
        import openai
    except ImportError as exc:  # pragma: no cover - depends on extras
        raise SpanReplayError(
            "replaying an OpenAI model needs the SDK — pip install selfevals[openai]"
        ) from exc
    if not os.environ.get("OPENAI_API_KEY"):
        raise SpanReplayError("OPENAI_API_KEY is not set")
    client = openai.OpenAI()
    msgs: list[dict[str, Any]] = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.extend(m for m in messages if m.get("role") != "system")
    try:
        # The OpenAI SDK's `messages` param is a large typed union; our plain
        # dicts are wire-compatible. Cast to Any rather than carry a type-ignore.
        resp = client.chat.completions.create(model=model, messages=cast(Any, msgs))
    except Exception as exc:  # audit:ignore[broad_exception_catches] — wrap provider SDK error (network/auth/rate-limit)
        raise SpanReplayError(f"openai call failed: {exc}") from exc
    content = resp.choices[0].message.content or ""
    usage = resp.usage
    return content, (usage.prompt_tokens if usage else 0), (usage.completion_tokens if usage else 0)


_CALLERS = {"anthropic": _call_anthropic, "openai": _call_openai}

# Curated model ids offered in the Playground picker, per provider. These are
# the ones with entries in the pricing table (`runner/pricing.py`), so the
# replay reports a real cost; the user can still type any other model string
# (which replays fine but reports $0 until it's registered in the price table).
_CURATED_MODELS: dict[str, list[str]] = {
    "anthropic": [
        "claude-opus-4-1",
        "claude-sonnet-4-6",
        "claude-sonnet-4-5",
        "claude-haiku-4-5",
    ],
    "openai": ["gpt-4o", "gpt-4o-mini", "o3", "o4-mini"],
}


def available_providers() -> list[dict[str, Any]]:
    """Which providers can be replayed here: SDK installed AND key present.

    Feeds the Playground picker so it only offers what will actually work,
    with the curated model list for each.
    """
    import importlib.util

    out: list[dict[str, Any]] = []
    for provider, key_env in (("anthropic", "ANTHROPIC_API_KEY"), ("openai", "OPENAI_API_KEY")):
        sdk_installed = importlib.util.find_spec(provider) is not None
        out.append(
            {
                "provider": provider,
                "available": sdk_installed and bool(os.environ.get(key_env)),
                "sdk_installed": sdk_installed,
                "has_key": bool(os.environ.get(key_env)),
                "models": _CURATED_MODELS.get(provider, []),
            }
        )
    return out


def replay_span(
    storage: StorageInterface,
    object_store: FilesystemObjectStore,
    *,
    workspace_id: str,
    trace_id: str,
    span_id: str,
    body: SpanReplayRequest,
    caller: Any = None,
) -> SpanReplayResponse:
    """Re-issue an LLM span's prompt against a chosen provider/model.

    `caller` overrides the provider call (tests inject a fake); production
    dispatches on `body.provider`.
    """
    with storage.open(workspace_id) as scope:
        trace = resolve_trace(scope, trace_id)
        span = _find_llm_span(trace, span_id)
        system = _resolve(
            object_store,
            inline=span.system_prompt_inline,
            pointer=span.system_prompt_pointer,
        )
        messages_raw = _resolve(
            object_store, inline=span.messages_inline, pointer=span.messages_pointer
        )
    messages = _messages_from_payload(messages_raw)

    provider = body.provider.strip().lower()
    call = caller or _CALLERS.get(provider)
    if call is None:
        raise SpanReplayError(f"unsupported provider {body.provider!r}")

    t0 = time.perf_counter()
    content, tokens_in, tokens_out = call(
        model=body.model, system=system, messages=messages, params=body.params or {}
    )
    duration_ms = int((time.perf_counter() - t0) * 1000)

    cost = estimate_cost(
        provider,
        body.model,
        TokenBreakdown(input=tokens_in, output=tokens_out, total=tokens_in + tokens_out),
    )
    return SpanReplayResponse(
        provider=provider,
        model=body.model,
        content=content,
        tokens_input=tokens_in,
        tokens_output=tokens_out,
        cost_usd=cost.total if cost is not None else None,
        duration_ms=duration_ms,
        original_model=span.model,
        original_provider=span.provider,
    )
