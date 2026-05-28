"""JSON rendering of an OptimizationResult.

Stable shape — version this dict before changing any key. Downstream
tooling (CI bots, dashboards) reads it.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from selfevals.reporter._metrics import compute_cost_time_summary

if TYPE_CHECKING:
    from selfevals.optimization.loop import IterationOutcome, OptimizationResult


_SCHEMA_VERSION = "1"


def render_json(result: OptimizationResult, *, indent: int | None = 2) -> str:
    """Serialize an OptimizationResult to a JSON string."""
    return json.dumps(to_dict(result), indent=indent, sort_keys=True, default=_default)


def to_dict(result: OptimizationResult) -> dict[str, Any]:
    exp = result.experiment
    best = result.best_iteration
    summary = compute_cost_time_summary(result)
    return {
        "schema_version": _SCHEMA_VERSION,
        "experiment": {
            "id": exp.id,
            "name": exp.name,
            "goal": exp.goal,
            "mode": str(exp.mode),
            "state": str(exp.state),
            "primary_metric": exp.target.primary.name,
            "primary_target": {
                "operator": exp.target.primary.operator,
                "value": exp.target.primary.value,
            },
            "guardrails": [
                {"name": g.name, "operator": g.operator, "value": g.value}
                for g in exp.target.guardrails
            ],
            "proposer_strategy": str(exp.proposer.strategy),
            "max_iterations": exp.run.max_iterations,
        },
        "termination": {
            "reason": result.terminated_reason or None,
            "iterations_run": len(result.iterations),
        },
        "cost_time": {
            "cost_total_usd": summary.cost_total_usd,
            "cost_per_iteration_usd": summary.cost_per_iteration_usd,
            "cost_per_case_usd": summary.cost_per_case_usd,
            "time_total_seconds": summary.time_total_seconds,
            "time_per_iteration_seconds": summary.time_per_iteration_seconds,
            "time_per_case_seconds": summary.time_per_case_seconds,
            "iterations": summary.iterations,
            "cases_run": summary.cases_run,
        },
        "best_iteration": _iteration_to_dict(best) if best is not None else None,
        "iterations": [_iteration_to_dict(it) for it in result.iterations],
    }


def _iteration_to_dict(it: IterationOutcome) -> dict[str, Any]:
    agg = it.aggregate
    return {
        "iteration": it.iteration,
        "hypothesis": it.proposal.hypothesis,
        "parameters": dict(it.proposal.parameters),
        "metrics": {
            "primary": {"name": agg.primary_metric, "value": agg.primary_value},
            "guardrails": dict(agg.guardrails),
            "reliability": dict(agg.reliability),
        },
        "failure_modes": dict(agg.failure_mode_counts),
        "funnel": {key: node.to_dict() for key, node in agg.funnel.items()},
        "totals": {
            "cost_usd": agg.total_cost_usd,
            "duration_ms": agg.total_duration_ms,
            "case_count": agg.case_count,
        },
        "decision": {
            "outcome": str(it.decision_record.outcome),
            "rationale": (
                it.iteration_record.decision.rationale
                if it.iteration_record.decision is not None
                else ""
            ),
        },
        "records": {
            "iteration_id": it.iteration_record.id,
            "decision_id": it.decision_record.id,
        },
    }


def _default(obj: Any) -> Any:
    # StrEnum already serializes via str(); this catches the unexpected.
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")
