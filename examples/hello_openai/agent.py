"""OpenAI-backed agent used by `experiment.yaml`.

`run` is the selfevals entrypoint. It receives an `AdapterRequest`, calls
OpenAI's Chat Completions API (or a deterministic fake when no API key is
present), and returns an `AdapterResponse`.

This mirrors `examples/hello_llm/agent.py` (Anthropic) so the two examples
stay comparable — same three cases, same graders, same temperature sweep.
The only difference is the provider call.

Design notes:

- `openai` is imported lazily inside `_call_openai` so the example
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

from selfevals.runner.adapters import AdapterRequest, AdapterResponse

DEFAULT_MODEL = "gpt-4o-mini"
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
    """Default entrypoint wired by `experiment.yaml`."""
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

    Injectable for tests. When `OPENAI_API_KEY` is missing or the OpenAI
    SDK is not installed, `fake_responder` is used.
    """
    responder = fake_responder or _default_fake_responder

    def _invoke(req: AdapterRequest) -> AdapterResponse:
        ctx = _prompt_context(req)
        if _openai_available():
            try:
                return _call_openai(ctx, model=model)
            except _OpenAICallError:
                return responder(ctx)
        return responder(ctx)

    return _invoke


def build_judge(
    *,
    fake_responder: FakeResponder | None = None,
    model: str = DEFAULT_MODEL,
) -> Callable[[AdapterRequest], AdapterResponse]:
    """Construct a judge `(AdapterRequest) -> AdapterResponse`."""
    responder = fake_responder or _default_fake_judge

    def _invoke(req: AdapterRequest) -> AdapterResponse:
        ctx = _judge_context(req)
        if _openai_available():
            try:
                return _call_openai_judge(ctx, model=model)
            except _OpenAICallError:
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


def _call_openai_judge(ctx: PromptContext, *, model: str) -> AdapterResponse:
    from openai import OpenAI  # type: ignore[import-not-found]

    client = OpenAI()
    system_prompt = (
        "You are a strict evaluator. Reply with a single JSON object "
        "containing keys: label (pass|fail|partial), reason, score, "
        "confidence. No prose outside the JSON."
    )
    try:
        completion = client.chat.completions.create(
            model=model,
            max_tokens=DEFAULT_MAX_TOKENS,
            temperature=0.0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": ctx.user_text},
            ],
        )
    except Exception as exc:
        raise _OpenAICallError(str(exc)) from exc
    text = completion.choices[0].message.content or ""
    usage = getattr(completion, "usage", None)
    return AdapterResponse(
        content=text,
        tokens_input=int(getattr(usage, "prompt_tokens", 0) or 0),
        tokens_output=int(getattr(usage, "completion_tokens", 0) or 0),
        stop_reason=getattr(completion.choices[0], "finish_reason", None),
        provider_metadata={"model": model, "judge": True},
    )


def _default_fake_judge(ctx: PromptContext) -> AdapterResponse:
    """Pass if the agent response embedded in the rubric prompt looks
    empathetic and offers a next step; fail otherwise."""
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
    """Best-effort JSON parse for extraction tasks."""
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


class _OpenAICallError(RuntimeError):
    """Internal sentinel: the OpenAI call failed but we want to fall back."""


def _openai_available() -> bool:
    """True only when both the SDK and an API key are present.

    Three states, handled distinctly so failures are legible:

    - SDK missing entirely -> `_warn_sdk_missing` once, then fall back to
      the fake. This is the "ran the example without the extra" path; the
      hint points at `pip install selfevals[openai]`.
    - SDK present but no `OPENAI_API_KEY` -> silent fall back to the fake.
      This is the intended offline-demo path, not an error.
    - Both present -> real call.
    """
    try:
        import openai  # noqa: F401
    except ImportError:
        _warn_sdk_missing()
        return False
    if not os.environ.get("OPENAI_API_KEY"):
        return False
    return True


_warned_sdk_missing = False


def _warn_sdk_missing() -> None:
    """Emit the install hint exactly once per process."""
    global _warned_sdk_missing
    if _warned_sdk_missing:
        return
    _warned_sdk_missing = True
    import sys

    print(
        "[hello_openai] the `openai` SDK is not installed; using the "
        "deterministic fake. To run against the real API:\n"
        "    pip install 'selfevals[openai]'   # bundles the SDK + tracing\n"
        "    export OPENAI_API_KEY=...",
        file=sys.stderr,
    )


def _call_openai(ctx: PromptContext, *, model: str) -> AdapterResponse:
    from openai import OpenAI  # optional dep — gated by _openai_available

    client = OpenAI()
    system_prompt = _system_prompt_for(ctx.task_hint)
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": DEFAULT_MAX_TOKENS,
        "temperature": ctx.temperature,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": ctx.user_text},
        ],
    }
    if ctx.top_p is not None:
        kwargs["top_p"] = ctx.top_p
    # Extraction asks for a JSON object; constrain the decoder so the
    # structured grader gets parseable output.
    if ctx.task_hint == "extraction":
        kwargs["response_format"] = {"type": "json_object"}
    try:
        completion = client.chat.completions.create(**kwargs)
    except Exception as exc:  # network, auth, rate-limit
        raise _OpenAICallError(str(exc)) from exc

    text = completion.choices[0].message.content or ""
    usage = getattr(completion, "usage", None)
    tokens_input = int(getattr(usage, "prompt_tokens", 0) or 0)
    tokens_output = int(getattr(usage, "completion_tokens", 0) or 0)
    structured = _maybe_parse_structured(ctx.task_hint, text)
    return AdapterResponse(
        content=text,
        structured_output=structured,
        tokens_input=tokens_input,
        tokens_output=tokens_output,
        stop_reason=getattr(completion.choices[0], "finish_reason", None),
        provider_metadata={"model": model, "temperature": ctx.temperature},
    )


_FAKE_TOKEN_COST = 0.000_001  # nominal; exercises the cost path without lying.


def _default_fake_responder(ctx: PromptContext) -> AdapterResponse:
    """Deterministic stand-in for the OpenAI API.

    Designed so the temperature sweep produces *different* grader
    outcomes — sentiment hedges to "mixed" when warm, extraction drifts
    above temperature 0, support reply is a fixed empathetic answer.
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
    return ctx.user_text, None


def _fake_sentiment(ctx: PromptContext) -> str:
    lowered = ctx.user_text.lower()
    positives = ("love", "great", "fantastic", "amazing", "happy")
    negatives = ("hate", "terrible", "awful", "broken", "disappointed")
    pos = any(w in lowered for w in positives)
    neg = any(w in lowered for w in negatives)
    if ctx.temperature >= 0.7:
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


def _system_prompt_for(task_hint: str) -> str:
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
    "build_judge",
    "build_runner",
    "judge",
    "run",
]
