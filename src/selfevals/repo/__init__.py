"""Repo loaders: read on-disk experiment specs into Pydantic models.

Canon §22 promises a code-reviewable `evals/` layout. This package
turns that layout into the in-memory objects the runtime needs.

Public API:

- `load_experiment_spec(path)` → ExperimentSpec
- `ExperimentSpec` carries Experiment + cases + a transport-tagged agent
  spec (`EmbeddedAgentSpec` / `CliAgentSpec` / `HttpAgentSpec`).
"""

from __future__ import annotations

from selfevals.repo.datasets import (
    build_dataset,
    compute_manifest_hash,
    compute_statistics,
    persist_dataset,
)
from selfevals.repo.loader import (
    AgentEntrypoint,
    AgentSpec,
    CliAgentSpec,
    DatasetSpec,
    EmbeddedAgentSpec,
    ExperimentSpec,
    HttpAgentSpec,
    InlineDatasetSource,
    LoaderError,
    RefDatasetSource,
    load_experiment_spec,
    resolve_agent_callable,
)

__all__ = [
    "AgentEntrypoint",
    "AgentSpec",
    "CliAgentSpec",
    "DatasetSpec",
    "EmbeddedAgentSpec",
    "ExperimentSpec",
    "HttpAgentSpec",
    "InlineDatasetSource",
    "LoaderError",
    "RefDatasetSource",
    "build_dataset",
    "compute_manifest_hash",
    "compute_statistics",
    "load_experiment_spec",
    "persist_dataset",
    "resolve_agent_callable",
]
