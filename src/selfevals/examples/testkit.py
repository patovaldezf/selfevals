"""Serializable example agents + judges used by the test suite.

Why these live in ``src/`` and not ``tests/``: the sharded executor resolves an
agent by its **import path** (``module:function``) in a worker process, then runs
it. A closure defined inside a test function cannot be addressed that way — it is
not importable. So the behaviors the suite needs (pong/miss by level, a fixed
pass/fail agent, a deterministic stand-in judge) are real module-level functions
here, exactly the shape a production agent takes::

    def run(req: AdapterRequest) -> AdapterResponse: ...

This is not test scaffolding bolted onto prod code — it is the same example-agent
surface as ``pingpong.py`` / ``showcase.py``, extended with the few extra fixed
behaviors the optimization-loop tests assert against. Driving tests through these
entrypoints means the suite exercises the real serialize → worker → execute path,
not an in-process shortcut.
"""

from __future__ import annotations

import json

from selfevals.runner.adapters import AdapterRequest, AdapterResponse


def pong_by_level(req: AdapterRequest) -> AdapterResponse:
    """Emit ``pong`` when ``model_params.level >= 0.5``, else ``miss``.

    The canonical improvable agent: the grid proposer can climb from level=0.0
    (fail) to level=1.0 (pass). Same behavior as ``pingpong.run`` — kept here so
    the loop tests have one import path for the whole family.
    """
    level = req.get_model_param("level", 0.0)
    content = "pong" if level >= 0.5 else "miss"
    return AdapterResponse(content=content, tokens_input=4, tokens_output=2)


def always_pong(_req: AdapterRequest) -> AdapterResponse:
    """Always pass (emit ``pong``), regardless of params."""
    return AdapterResponse(content="pong", tokens_input=4, tokens_output=2)


def always_miss(_req: AdapterRequest) -> AdapterResponse:
    """Always fail (emit ``miss``), regardless of params."""
    return AdapterResponse(content="miss", tokens_input=4, tokens_output=2)


def mock_judge(req: AdapterRequest) -> AdapterResponse:
    """Deterministic stand-in for an LLM judge.

    Reads the agent response baked into the rendered rubric prompt (a real judge
    does the same) and returns JSON ``label=pass`` iff the response said ``pong``.
    The judge contract IS the adapter contract: rubric prompt in, JSON verdict out.
    """
    prompt = req.input["messages"][0]["content"]
    decided_pass = "pong" in prompt and "miss" not in prompt.lower().split("agent response:")[-1]
    payload = {
        "label": "pass" if decided_pass else "fail",
        "reason": "agent emitted pong" if decided_pass else "did not emit pong",
        "score": 1.0 if decided_pass else 0.0,
        "confidence": 0.95,
    }
    return AdapterResponse(content=json.dumps(payload))
