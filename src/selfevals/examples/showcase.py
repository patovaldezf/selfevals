"""Kitchen-sink example agent for the `showcase` experiment.

Where `pingpong` is the minimal "hello world" (an echo agent, one deterministic
grader), `showcase` exercises **every grading surface** the framework ships so a
developer — or an agent reading the repo — has one runnable, copyable example
that shows:

- a rich ``structured_output`` consumed by the path selector (``graders/_select``),
- ``tool_uses`` that the executor turns into ``ToolCallSpan``s (so ``tool_called``
  and ``span_exists`` matches have something to see),
- the funnel grader with a ``gate`` that short-circuits its children,
- ``set_match`` (set-vs-set scoring against ``expected.must_include`` + ``aliases``),
- ``judge_panel`` running fully offline against the deterministic ``judge`` below.

Like pingpong, behavior is driven by ``model_params.level`` so the grid proposer
demonstrates a real improvement path:

- ``level < 0.5`` → an *incomplete* resolution (``resolved={}``, no candidates).
  The funnel's gate level (``found``) fails and its descendants are SKIPPED — that
  is the short-circuit, visible in the breakdown.
- ``level >= 0.5`` → a *complete* resolution. Every level passes.

A real agent replaces ``run`` with a call to Anthropic / OpenAI / its framework
of choice. The contract is the same::

    def run(req: AdapterRequest) -> AdapterResponse | str: ...

and a real judge replaces ``judge`` with an LLM call. The judge contract is also
just the adapter contract: it receives the rendered rubric prompt as its input
message and returns a JSON verdict as ``content``.
"""

from __future__ import annotations

import json
from typing import Any

from selfevals.runner.adapters import AdapterRequest, AdapterResponse, AdapterToolUse


def run(req: AdapterRequest) -> AdapterResponse:
    """Resolve the requested SKUs, emitting a structured contract + tool calls.

    The shape of ``structured_output`` is what the funnel's ``extract`` paths and
    ``set_match``'s ``detected`` selector read; keep it in sync with the levels
    declared in ``example_showcase.yaml``.
    """
    level = req.get_model_param("level", 0.0)
    complete = level >= 0.5

    # Tool calls are emitted in both branches: the executor records each as a
    # ToolCallSpan(tool_name=...), so `tool_called` / `span_exists` see them
    # regardless of whether the resolution succeeded.
    tool_uses = [
        AdapterToolUse(tool="search", tool_use_id="t1", args={"query": "skus"}),
        AdapterToolUse(tool="resolve", tool_use_id="t2", args={"ids": ["sku-42", "sku-7"]}),
    ]

    if complete:
        structured: dict[str, Any] = {
            "resolved": {"id": "sku-42", "status": "ok"},
            "detected": ["sku-42", "sku-7"],
            "candidates": [
                {"id": "sku-42", "score": 0.9},
                {"id": "sku-7", "score": 0.4},
            ],
            "category": "electronics",
            "summary": "Resolved 2 of 2 products.",
        }
        content = "Resolved 2 of 2 products: sku-42, sku-7."
    else:
        structured = {
            "resolved": {},
            "detected": [],
            "candidates": [],
            "category": "unknown",
            "summary": "Resolved 0 of 2 products.",
        }
        content = "Resolved 0 of 2 products."

    return AdapterResponse(
        content=content,
        structured_output=structured,
        tool_uses=tool_uses,
        tokens_input=12,
        tokens_output=8,
    )


def judge(req: AdapterRequest) -> AdapterResponse:
    """Deterministic judge for `judge_panel`/`llm_judge` — runs offline, no API.

    The grader hands the judge a rendered rubric prompt as its single user
    message (``input.messages[0].content``), which embeds the agent's response.
    A real judge would reason over it with an LLM; here we inspect the embedded
    response text so the verdict is stable (and `judge_panel`'s majority is
    reproducible). Returns a JSON object with ``label``/``reason``/``score`` —
    the shape ``llm_judge._parse_judge_output`` expects.
    """
    messages = req.input.get("messages", [])
    prompt = messages[0].get("content", "") if messages else ""
    resolved_all = "Resolved 2 of 2" in prompt
    verdict = (
        {"label": "pass", "reason": "resolution complete", "score": 1.0}
        if resolved_all
        else {"label": "fail", "reason": "incomplete resolution", "score": 0.0}
    )
    return AdapterResponse(content=json.dumps(verdict), tokens_input=20, tokens_output=6)
