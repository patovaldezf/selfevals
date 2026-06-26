"""A deterministic pairwise judge for the tournament API tests.

Resolved by dotted path (`tests.api._tournament_judge:judge`) the same way a
real `judge_entrypoint` is. It imposes a fixed quality order a > b > c by reading
the two responses out of the comparative prompt and preferring the higher one.
"""

from __future__ import annotations

import json

from selfevals.runner.adapters import AdapterRequest, AdapterResponse

_ORDER = {"out-a": 3, "out-b": 2, "out-c": 1}


def judge(req: AdapterRequest) -> AdapterResponse:
    prompt = req.input["messages"][0]["content"]
    present = [t for t in _ORDER if t in prompt]
    present.sort(key=lambda t: prompt.find(t))
    first, second = present[0], present[1]
    preferred = "a" if _ORDER[first] > _ORDER[second] else "b"
    return AdapterResponse(
        content=json.dumps({"preferred": preferred, "margin": 0.5, "reason": "quality"})
    )
