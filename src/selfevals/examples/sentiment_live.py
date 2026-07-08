"""Live sentiment-classification agent — calls Claude for real.

Unlike `pingpong`/`showcase` (deterministic, offline, `sandbox: mock`), this
agent hits the Anthropic API on every case so you can watch a *real* trace
populate live in the web UI: the model request span opens, the LLM call runs
for a few seconds, then closes with the structured verdict. Pair it with
`evals/experiments/example_sentiment_live.yaml`.

Contract is the standard adapter shape::

    def run(req: AdapterRequest) -> AdapterResponse

The case `input.messages[0].content` is a product review. The agent asks Claude
to classify it into one of {positive, negative, neutral} plus a confidence, and
returns the label under `structured_output["detected"]` so a `set_match` grader
can score it against `expected.must_include`.

Structured output is forced via a single-tool `tool_choice`, the standard
Anthropic SDK pattern for schema-constrained replies — see
`examples/hello_llm/agent.py` for the plain-text call shape this borrows from.
"""

from __future__ import annotations

import os
from typing import Any

from selfevals.runner.adapters import AdapterRequest, AdapterResponse

MODEL = "claude-opus-4-8"

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sentiment": {
            "type": "string",
            "enum": ["positive", "negative", "neutral"],
        },
        "confidence": {"type": "number"},
        "rationale": {"type": "string"},
    },
    "required": ["sentiment", "confidence", "rationale"],
}

_TOOL_NAME = "classify_sentiment"


class _AnthropicCallError(RuntimeError):
    """Raised when the Anthropic SDK call fails (network, auth, rate-limit)."""


def run(req: AdapterRequest) -> AdapterResponse:
    """Classify the review's sentiment with a real Claude call."""
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - depends on extras
        raise RuntimeError(
            "sentiment_live needs the anthropic SDK — `pip install selfevals[anthropic]` "
            "or `uv sync --all-extras`."
        ) from exc

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is not set; sentiment_live needs a real key.")

    messages = req.input.get("messages", [])
    review = messages[0].get("content", "") if messages else ""

    client = anthropic.Anthropic()
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=(
                "You classify the sentiment of a customer product review. "
                "Think carefully about tone, sarcasm, mixed signals, and implied "
                "dissatisfaction before deciding. Call the classify_sentiment tool "
                "with your verdict."
            ),
            messages=[{"role": "user", "content": f"Review:\n{review}"}],
            tools=[
                {
                    "name": _TOOL_NAME,
                    "description": "Record the sentiment classification for the review.",
                    "input_schema": _SCHEMA,
                }
            ],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
        )
    except Exception as exc:  # audit:ignore[broad_exception_catches] — wrap provider SDK error (network, auth, rate-limit) into a domain error
        raise _AnthropicCallError(str(exc)) from exc

    tool_use = next((b for b in response.content if b.type == "tool_use"), None)
    parsed = dict(tool_use.input) if tool_use is not None else {}
    label = parsed.get("sentiment", "neutral")

    usage = response.usage
    return AdapterResponse(
        content=f"{label}: {parsed.get('rationale', '')}",
        structured_output={"detected": [label], **parsed},
        stop_reason=response.stop_reason,
        tokens_input=usage.input_tokens,
        tokens_output=usage.output_tokens,
        provider_metadata={"model": response.model},
    )
