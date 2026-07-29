"""The `bakeoff` example runs fully offline and proves the agents axis.

Pins the packaged provider bake-off end-to-end so its promise can't silently
rot: two whole agents declared under `search_space.agents`, one iteration each,
with the premium provider beating the budget one on quality while costing more.
No branches, no worktrees, no network, no API key.
"""

from __future__ import annotations

import json
from importlib import resources

import pytest
import yaml

from selfevals.repo.loader import ExperimentSpec, build_spec_from_mapping
from selfevals.runner.launch import build_loop

_WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _spec() -> ExperimentSpec:
    """Load the packaged spec, inlining its dataset (no cwd-relative paths)."""
    root = resources.files("selfevals.examples").joinpath("evals")
    raw = yaml.safe_load(
        root.joinpath("experiments", "example_bakeoff.yaml").read_text(encoding="utf-8")
    )
    rows = [
        json.loads(line)
        for line in root.joinpath("datasets", "bakeoff.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    raw["dataset"] = {"cases_inline": rows, "name": "bakeoff inline", "dataset_type": "capability"}
    return build_spec_from_mapping(raw, workspace_id=_WS)


@pytest.mark.asyncio
async def test_bakeoff_runs_both_providers_and_ranks_them() -> None:
    loop = build_loop(_spec(), scope=None, repetitions_per_case=1)
    result = await loop.run()

    assert len(result.iterations) == 2, "one iteration per declared agent"

    # Each iteration bound a different agent — the binding axis did its job.
    names = [it.proposal.parameters["agent"]["name"] for it in result.iterations]
    assert names == ["premium", "budget"]

    # And they scored differently: the budget provider misses the implicit cue.
    scores = [it.aggregate.primary_value for it in result.iterations]
    assert scores[0] == 1.0, "premium answers both cases"
    assert scores[1] == 0.5, "budget only answers the obvious one"

    # Cost is attributed per agent, which is the other half of the decision.
    costs = [it.aggregate.total_cost_usd for it in result.iterations]
    assert costs[0] > costs[1], "the premium provider should cost more"
