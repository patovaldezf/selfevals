"""Read queries over the Postgres store, shaped for the web UI.

We don't add an ORM. We open a `WorkspaceScope` per request, list
entities, and project them into the view models in
`selfevals.api.schemas`. The single non-trivial bit is rebuilding
the `OptimizationResult` JSON via the existing reconstruction helper
in `cli.commands` so the web reuses the exact same shape the
reporter emits.

Split by resource under this package; re-exported here so existing
`from selfevals.api.queries import X` imports keep working unchanged.
"""

from __future__ import annotations

from selfevals.api.queries._shared import span_summary as _span_summary
from selfevals.api.queries.datasets import dataset_detail, list_datasets
from selfevals.api.queries.experiments import (
    AnchorPoint,
    anchor_set_history,
    experiment_cases,
    experiment_decisions,
    experiment_detail,
    experiment_iterations,
    iteration_detail,
    list_experiments,
    load_compare,
    load_iteration_funnel,
)
from selfevals.api.queries.traces import experiment_results, load_thread, load_trace
from selfevals.api.queries.workspaces import list_workspaces, workspace_detail

__all__ = [
    "AnchorPoint",
    "_span_summary",
    "anchor_set_history",
    "dataset_detail",
    "experiment_cases",
    "experiment_decisions",
    "experiment_detail",
    "experiment_iterations",
    "experiment_results",
    "iteration_detail",
    "list_datasets",
    "list_experiments",
    "list_workspaces",
    "load_compare",
    "load_iteration_funnel",
    "load_thread",
    "load_trace",
    "workspace_detail",
]
