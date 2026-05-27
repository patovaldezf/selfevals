"""ContextVars used by the SDK to tag spans with run-scoped metadata.

The framework side (`selfevals run`) sets SELFEVALS_ITERATION_ID / project
via env vars on the child process; the SDK side mirrors those into module
state so OTel resource attributes carry them on every span.

Kept dependency-free on purpose — `selfevals.init()` may be called from
user code in environments where the telemetry extras aren't installed.
"""

from __future__ import annotations

import os
from contextvars import ContextVar
from dataclasses import dataclass

_PROJECT_ENV = "SELFEVALS_PROJECT"
_ITERATION_ENV = "SELFEVALS_ITERATION_ID"
_RUN_ENV = "SELFEVALS_RUN_ID"


@dataclass(frozen=True)
class RunTags:
    project: str
    iteration_id: str | None
    run_id: str | None


_current_tags: ContextVar[RunTags | None] = ContextVar("selfevals_current_tags", default=None)


def set_tags(tags: RunTags) -> None:
    _current_tags.set(tags)


def current_tags() -> RunTags | None:
    return _current_tags.get()


def tags_from_env(project_fallback: str) -> RunTags:
    return RunTags(
        project=os.environ.get(_PROJECT_ENV, project_fallback),
        iteration_id=os.environ.get(_ITERATION_ENV),
        run_id=os.environ.get(_RUN_ENV),
    )
