"""Fixture agent that returns a bare str (not AdapterResponse).

Used by `test_run_user_callable_returning_str_is_wrapped` to exercise
the convenience path where the user's callable returns a string and the
CLI wrapper coerces it into an AdapterResponse.
"""

from __future__ import annotations

from selfevals.runner.adapters import AdapterRequest


def run(req: AdapterRequest) -> str:
    return "pong"
