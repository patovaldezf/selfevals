"""Anthropic-backed agent used by `experiment.yaml`.

`run` is the boostrap entrypoint. It receives an `AdapterRequest`, calls
the Anthropic Messages API (or a deterministic fake when no API key is
present), and returns an `AdapterResponse`.

Design notes:

- Anthropic is imported lazily inside `_call_anthropic` so the example
  remains importable in environments without the SDK installed.
- The fake response generator is injected (`fake_responder` parameter on
  `build_runner`) rather than referenced as module-level state, so tests
  can drive the fake without monkey-patching globals.
- Temperature and `top_p` are pulled from `req.parameters["model_params"]`
  so the `GridProposer` actually moves them. Both the real API call and
  the fake honour the same parameter shape.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from bootstrap.runner.adapters import AdapterRequest, AdapterResponse

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_MAX_TOKENS = 256

FakeResponder = Callable[["PromptContext"], "AdapterResponse"]


@dataclass(frozen=True)
class PromptContext:
    """Inputs the responder (real or fake) needs to produce a reply."""

    case_id: str
    user_text: str
    task_hint: str
    temperature: float
    top_p: float | None


def run(req: AdapterRequest) -> AdapterResponse:
    """Default entrypoint wired by `experiment.yaml`.

    Resolves the responder once per call (cheap; no module-level state),
    extracts the latest user turn, and returns the adapter response.
    """
    return build_runner()(req)


def judge(req: AdapterRequest) -> AdapterResponse:
    """LLM-judge entrypoint wired by `experiment.yaml` for the rubric grader.

    The judge is forced to reply with a JSON object containing `label`,
    `reason`, `score`, `confidence` — `LLMJudgeGrader._parse_judge_output`
    fails loudly on any other shape. In fake mode we synthesize a passing
    JSON payload when the agent response looks like a polite support
    reply, otherwise we fail it.
    """
    return build_judge()(req)


def build_runner(
    *,
    fake_responder: FakeResponder | None = None,
    model: str = DEFAULT_MODEL,
) -> Callable[[AdapterRequest], AdapterResponse]:
    """Construct an `(AdapterRequest) -> AdapterResponse` callable.

    Injectable for tests. When `ANTHROPIC_API_KEY` is missing or the
    Anthropic SDK is not installed, `fake_responder` is used. `model`
    is configurable so smoke tests can pin an arbitrary value.
    """
    responder = fake_responder or _default_fake_responder

    def _invoke(req: AdapterRequest) -> AdapterResponse:
        ctx = _prompt_context(req)
        if _anthropic_available():
            try:
                return _call_anthropic(ctx, model=model)
            except _AnthropicCallError:
                # Anthropic SDK installed but the call failed (network,
                # auth, rate-limit). Falling back to the fake keeps the
                # example smoke-testable without masking the real error
                # for production users — they will see it in the trace
                # provider_metadata once we wire that in.
                return responder(ctx)
        return responder(ctx)

    return _invoke


def build_judge(
    *,
    fake_responder: FakeResponder | None = None,
    model: str = DEFAULT_MODEL,
) -> Callable[[AdapterRequest], AdapterResponse]:
    """Construct a judge `(AdapterRequest) -> AdapterResponse`.

    Same fall-back contract as `build_runner`: real Anthropic if the SDK
    + API key are present, otherwise the injected fake. The fake is a
    rubric inspector that returns valid judge JSON.
    """
    responder = fake_responder or _default_fake_judge

    def _invoke(req: AdapterRequest) -> AdapterResponse:
        ctx = _judge_context(req)
        if _anthropic_available():
            try:
                return _call_anthropic_judge(ctx, model=model)
            except _AnthropicCallError:
                return responder(ctx)
        return responder(ctx)

    return _invoke


def _judge_context(req: AdapterRequest) -> PromptContext:
    """Judge sees the rubric prompt directly; temperature stays at 0 for
    a stable verdict regardless of what the proposer set for the agent."""
    user_text = _latest_user_text(req.input)
    return PromptContext(
        case_id=req.case_id,
        user_text=user_text,
        task_hint="judge",
        temperature=0.0,
        top_p=None,
    )


def _call_anthropic_judge(ctx: PromptContext, *, model: str) -> AdapterResponse:
    import anthropic  # type: ignore[import-not-found]

    client = anthropic.Anthropic()
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": DEFAULT_MAX_TOKENS,
        "temperature": 0.0,
        "system": (
            "You are a strict evaluator. Reply with a single JSON object "
            "containing keys: label (pass|fail|partial), reason, score, "
            "confidence. No prose outside the JSON."
        ),
        "messages": [{"role": "user", "content": ctx.user_text}],
    }
    try:
        message = client.messages.create(**kwargs)
    except Exception as exc:
        raise _AnthropicCallError(str(exc)) from exc
    text = _join_text_blocks(message.content)
    usage = getattr(message, "usage", None)
    return AdapterResponse(
        content=text,
        tokens_input=int(getattr(usage, "input_tokens", 0) or 0),
        tokens_output=int(getattr(usage, "output_tokens", 0) or 0),
        stop_reason=getattr(message, "stop_reason", None),
        provider_metadata={"model": model, "judge": True},
    )


def _default_fake_judge(ctx: PromptContext) -> AdapterResponse:
    """Pass if the agent response embedded in the rubric prompt looks
    empathetic and offers a next step; fail otherwise.

    We key off lexical signals only — the goal is a deterministic, useful
    grader for the no-credentials demo, not a real eval.
    """
    haystack = ctx.user_text.lower()
    empathic = any(token in haystack for token in ("sorry", "apologi", "thanks for"))
    actionable = any(
        token in haystack for token in ("refund", "replacement", "which would you prefer")
    )
    if empathic and actionable:
        payload = {
            "label": "pass",
            "reason": "acknowledges the problem and offers a next step",
            "score": 0.9,
            "confidence": 0.8,
        }
    else:
        payload = {
            "label": "fail",
            "reason": "missing acknowledgement or actionable next step",
            "score": 0.2,
            "confidence": 0.7,
        }
    body = json.dumps(payload)
    return AdapterResponse(
        content=body,
        tokens_input=max(len(ctx.user_text.split()), 1),
        tokens_output=max(len(body.split()), 1),
        cost_usd=_FAKE_TOKEN_COST * max(len(body.split()), 1),
        provider_metadata={"model": "fake", "judge": True},
    )


def _maybe_parse_structured(task_hint: str, text: str) -> dict[str, Any] | None:
    """Best-effort JSON parse for extraction tasks.

    For other tasks we leave `structured_output` as None — the
    `DeterministicGrader` only invokes the structured rule when
    `expected.structured_output` is set on the case, so this is safe.
    """
    if task_hint != "extraction":
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _prompt_context(req: AdapterRequest) -> PromptContext:
    model_params = req.parameters.get("model_params") or {}
    if not isinstance(model_params, dict):
        model_params = {}
    user_text = _latest_user_text(req.input)
    task_hint = str(req.metadata.get("task_hint") or req.input.get("task_hint") or "")
    return PromptContext(
        case_id=req.case_id,
        user_text=user_text,
        task_hint=task_hint,
        temperature=float(model_params.get("temperature", 0.0)),
        top_p=_optional_float(model_params.get("top_p")),
    )


def _latest_user_text(payload: dict[str, Any]) -> str:
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            chunks = [c.get("text", "") for c in content if isinstance(c, dict)]
            return " ".join(chunk for chunk in chunks if chunk)
    return ""


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# --- Anthropic call --------------------------------------------------------


class _AnthropicCallError(RuntimeError):
    """Internal sentinel: the Anthropic call failed but we want to fall back."""


def _anthropic_available() -> bool:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def _call_anthropic(ctx: PromptContext, *, model: str) -> AdapterResponse:
    import anthropic  # optional dep — gated by _anthropic_available

    client = anthropic.Anthropic()
    system_prompt = _system_prompt_for(ctx.task_hint)
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": DEFAULT_MAX_TOKENS,
        "temperature": ctx.temperature,
        "system": system_prompt,
        "messages": [{"role": "user", "content": ctx.user_text}],
    }
    if ctx.top_p is not None:
        kwargs["top_p"] = ctx.top_p
    try:
        message = client.messages.create(**kwargs)
    except Exception as exc:  # network, auth, rate-limit
        raise _AnthropicCallError(str(exc)) from exc

    text = _join_text_blocks(message.content)
    usage = getattr(message, "usage", None)
    tokens_input = int(getattr(usage, "input_tokens", 0) or 0)
    tokens_output = int(getattr(usage, "output_tokens", 0) or 0)
    structured = _maybe_parse_structured(ctx.task_hint, text)
    return AdapterResponse(
        content=text,
        structured_output=structured,
        tokens_input=tokens_input,
        tokens_output=tokens_output,
        stop_reason=getattr(message, "stop_reason", None),
        provider_metadata={"model": model, "temperature": ctx.temperature},
    )


def _join_text_blocks(blocks: Any) -> str:
    if isinstance(blocks, str):
        return blocks
    if not isinstance(blocks, list):
        return ""
    parts: list[str] = []
    for block in blocks:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


# --- Fake responder --------------------------------------------------------


_FAKE_TOKEN_COST = 0.000_001  # nominal; exercises the cost path without lying.


def _default_fake_responder(ctx: PromptContext) -> AdapterResponse:
    """Deterministic stand-in for the Anthropic API.

    The fake is designed so the temperature sweep produces *different*
    grader outcomes — without that, the example would optimize over a
    no-op search space and obscure the point. The strategy:

    - sentiment: cool temperatures favour the correct label; warm ones
      hedge with "mixed", which fails `must_include`.
    - extraction: structured output is exact at temperature == 0 and
      drifts (extra/wrong fields) above that.
    - support reply: a single canned, decent answer regardless of
      temperature — judged by the LLM judge fake, which always passes
      empathetic answers.
    """
    text, structured = _fake_payload_for(ctx)
    return AdapterResponse(
        content=text,
        structured_output=structured,
        tokens_input=max(len(ctx.user_text.split()), 1),
        tokens_output=max(len(text.split()), 1),
        cost_usd=_FAKE_TOKEN_COST * max(len(text.split()), 1),
        provider_metadata={"model": "fake", "temperature": ctx.temperature},
    )


def _fake_payload_for(ctx: PromptContext) -> tuple[str, dict[str, Any] | None]:
    hint = ctx.task_hint
    if hint == "sentiment":
        return _fake_sentiment(ctx), None
    if hint == "extraction":
        text, struct = _fake_extraction(ctx)
        return text, struct
    if hint == "support_reply":
        return _fake_support_reply(ctx), None
    # Unknown task — echo for safety.
    return ctx.user_text, None


def _fake_sentiment(ctx: PromptContext) -> str:
    lowered = ctx.user_text.lower()
    positives = ("love", "great", "fantastic", "amazing", "happy")
    negatives = ("hate", "terrible", "awful", "broken", "disappointed")
    pos = any(w in lowered for w in positives)
    neg = any(w in lowered for w in negatives)
    if ctx.temperature >= 0.7:
        # Warm: hedges into a noncommittal label.
        return "mixed"
    if pos and not neg:
        return "positive"
    if neg and not pos:
        return "negative"
    return "neutral"


def _fake_extraction(ctx: PromptContext) -> tuple[str, dict[str, Any]]:
    expected = {"city": "Berlin", "date": "2026-06-12", "attendees": 2}
    if ctx.temperature == 0.0:
        struct = dict(expected)
    elif ctx.temperature < 0.7:
        struct = {**expected, "attendees": 3}
    else:
        struct = {"city": "Berlin", "date": "2026-06-12"}
    return json.dumps(struct), struct


def _fake_support_reply(ctx: PromptContext) -> str:
    return (
        "Thanks for reaching out — I'm sorry the order hasn't arrived yet. "
        "I've checked the tracking and can offer either a refund or a "
        "replacement shipped today. Which would you prefer?"
    )


# --- System prompts --------------------------------------------------------


def _system_prompt_for(task_hint: str) -> str:
    """Pick a system prompt by task hint.

    Kept here (not in the YAML) so the experiment spec is free to vary
    `model_params` only — moving prompts into the search space is a
    follow-up that needs `editable.prompt = true` plus a prompt proposer.
    """
    if task_hint == "sentiment":
        return (
            "You classify a customer review as exactly one of: positive, "
            "negative, neutral. Reply with the single lowercase label."
        )
    if task_hint == "extraction":
        return (
            "Extract a JSON object with keys city (string), date (YYYY-MM-DD), "
            "and attendees (integer). Reply with JSON only, no prose."
        )
    if task_hint == "support_reply":
        return (
            "You are a polite, concise customer-support agent. Acknowledge "
            "the problem, propose two next steps, and ask which the user prefers."
        )
    return "You are a helpful assistant."


__all__ = [
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MODEL",
    "FakeResponder",
    "PromptContext",
    "build_runner",
    "run",
]
