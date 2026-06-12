"""The `showcase` example runs fully offline and exercises every grader.

Pins the kitchen-sink example end-to-end so its DX promise can't silently rot:
the grid proposer improves from level=0.0 (funnel gate fails → children skipped)
to level=1.0 (every level passes), and the funnel breakdown rolls up the keys
each match kind contributes. Runs against the mock sandbox with the deterministic
agent + judge — no network, no API key.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from selfevals.repo.loader import build_spec_from_mapping
from selfevals.runner.launch import build_loop

_WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _spec() -> object:
    repo_root = Path(__file__).resolve().parents[2]
    raw = yaml.safe_load((repo_root / "evals/experiments/example_showcase.yaml").read_text())
    rows = [
        json.loads(line)
        for line in (repo_root / "evals/datasets/showcase.jsonl").read_text().splitlines()
        if line.strip()
    ]
    raw["dataset"] = {"cases_inline": rows, "name": "showcase inline", "dataset_type": "capability"}
    return build_spec_from_mapping(raw, workspace_id=_WS)


@pytest.mark.asyncio
async def test_showcase_proposer_improves_and_funnel_short_circuits() -> None:
    loop = build_loop(_spec(), scope=None, repetitions_per_case=1)  # type: ignore[arg-type]
    result = await loop.run()

    # Grid sweeps level=[0.0, 1.0]: incomplete → complete resolution.
    assert len(result.iterations) == 2
    by_level = {
        it.proposal.parameters.get("model_params", {}).get("level"): it for it in result.iterations
    }
    low = by_level[0.0]
    high = by_level[1.0]

    # The proposer improves: gate fails at level 0.0, passes at level 1.0.
    assert low.aggregate.primary_value < high.aggregate.primary_value
    assert high.aggregate.primary_value == pytest.approx(1.0)

    # The funnel rolled up. At the winning iteration every level passed; the
    # gate (`found`) and its children (one per match kind) are present.
    funnel = high.aggregate.funnel
    assert funnel, "expected a rolled-up funnel breakdown"
    found = _find_node(funnel, "found")
    assert found is not None and found.mean_score == pytest.approx(1.0)
    child_keys = _descendant_keys(found)
    for key in (
        "top_is_sku42",
        "resolved_ok",
        "right_category",
        "used_search",
        "has_tool_span",
        "set_inside",
        "panel_ref",
    ):
        assert key in child_keys, f"missing funnel level {key!r}"


@pytest.mark.asyncio
async def test_showcase_confusion_matrix_tracks_category_class() -> None:
    loop = build_loop(_spec(), scope=None, repetitions_per_case=1)  # type: ignore[arg-type]
    result = await loop.run()
    by_level = {
        it.proposal.parameters.get("model_params", {}).get("level"): it for it in result.iterations
    }
    low = by_level[0.0]
    high = by_level[1.0]

    # The `confusion` grader (`category_class`) scores structured_output["category"]
    # against expected.outcome="electronics". At level<0.5 the agent emits
    # "unknown" (off-diagonal); at level>=0.5 "electronics" (diagonal).
    assert low.aggregate.confusion is not None
    assert low.aggregate.confusion.to_nested() == {"electronics": {"unknown": 3}}
    assert high.aggregate.confusion is not None
    assert high.aggregate.confusion.to_nested() == {"electronics": {"electronics": 3}}
    assert high.aggregate.confusion.accuracy == pytest.approx(1.0)
    assert high.aggregate.confusion.macro_f1 == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_showcase_gate_short_circuits_at_low_level() -> None:
    loop = build_loop(_spec(), scope=None, repetitions_per_case=1)  # type: ignore[arg-type]
    result = await loop.run()
    low = next(
        it for it in result.iterations if it.proposal.parameters.get("model_params", {}).get("level") == 0.0
    )
    # The gate's failure mode surfaces; the resolution never completed.
    assert low.aggregate.primary_value == pytest.approx(0.0)
    found = _find_node(low.aggregate.funnel, "found")
    assert found is not None
    assert found.mean_score == pytest.approx(0.0)


def _find_node(funnel: dict[str, object], key: str) -> object | None:
    """Depth-first search for a rolled-up funnel node by key."""
    for node_key, node in funnel.items():
        if node_key == key:
            return node
        found = _find_node(getattr(node, "children", {}), key)
        if found is not None:
            return found
    return None


def _descendant_keys(node: object) -> set[str]:
    keys: set[str] = set()
    for child_key, child in getattr(node, "children", {}).items():
        keys.add(child_key)
        keys |= _descendant_keys(child)
    return keys
