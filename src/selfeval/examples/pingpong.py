"""Trivial example agent for the pingpong experiment.

It returns "pong" when the proposer cranks `model_params.level >= 0.5`,
and "miss" otherwise. That lets the grid proposer demonstrate a real
improvement path from level=0.0 (fail) to level=1.0 (pass).

Real agents will replace this with a function that calls Anthropic /
OpenAI / their framework of choice. The contract is the same:

    def run(req: AdapterRequest) -> AdapterResponse | str: ...
"""

from __future__ import annotations

from selfeval.runner.adapters import AdapterRequest, AdapterResponse


def run(req: AdapterRequest) -> AdapterResponse:
    level = req.parameters.get("model_params", {}).get("level", 0.0)
    content = "pong" if level >= 0.5 else "miss"
    return AdapterResponse(content=content, tokens_input=4, tokens_output=2)
