"""Repo loaders: read on-disk experiment specs into Pydantic models.

Canon §22 promises a code-reviewable `evals/` layout. This package
turns that layout into the in-memory objects the runtime needs.

Public API:

- `load_experiment_spec(path)` → ExperimentSpec
- `ExperimentSpec` carries Experiment + cases + agent entrypoint string.
"""

from __future__ import annotations

from selfevals.repo.loader import (
    AgentEntrypoint,
    ExperimentSpec,
    LoaderError,
    load_experiment_spec,
    resolve_agent_callable,
)

__all__ = [
    "AgentEntrypoint",
    "ExperimentSpec",
    "LoaderError",
    "load_experiment_spec",
    "resolve_agent_callable",
]
