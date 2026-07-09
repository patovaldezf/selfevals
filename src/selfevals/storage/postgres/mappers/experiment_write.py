"""Free functions for `ExperimentMapper.upsert` — row values + child tables.

Split out of `experiment.py` (same free-function-delegate pattern as
`trace_spans_write.py`/`trace_spans_read.py`): pure functions that take
`cur`/the entity and write, with no `self` — `ExperimentMapper.upsert` stays
the single call site, just thinner.
"""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from selfevals.schemas._base import EntityRef
from selfevals.schemas.experiment import Experiment


def _ref_pair(ref: EntityRef | None) -> tuple[str | None, int | None]:
    if ref is None:
        return None, None
    return ref.id, ref.version


def experiment_row_values(shared: list[Any], e: Experiment) -> list[Any]:
    """Flatten `e`'s nested specs into the `_ALL_COLUMNS`-ordered value list."""
    risk_id, risk_ver = _ref_pair(e.frozen.risk_registry)
    feat_id, feat_ver = _ref_pair(e.frozen.feature_registry)
    adv_id, adv_ver = _ref_pair(e.judge_defenses.adversarial_dataset)
    panel = e.judge_defenses.panel
    cf = e.judge_defenses.counterfactuals
    hsc = e.judge_defenses.human_spot_check
    om = e.judge_defenses.outcome_metrics
    return [
        *shared,
        e.name,
        e.goal,
        e.mode.value,
        e.state.value,
        e.content_hash,
        list(e.taxonomy.target_features),
        list(e.taxonomy.target_levels),
        [d.value for d in e.taxonomy.dataset_types],
        e.datasets.optimization.id,
        e.datasets.optimization.version,
        e.target.primary.name,
        e.target.primary.operator,
        e.target.primary.value,
        e.target.primary_grader,
        e.editable.prompt,
        e.editable.model_params,
        e.editable.model_choice,
        e.editable.tool_descriptions,
        e.editable.tool_code,
        e.editable.workflow_graph,
        e.editable.skills,
        e.editable.dataset,
        e.editable.graders,
        Jsonb(e.search_space.model_params),
        Jsonb(e.search_space.prompt_variables),
        Jsonb(e.search_space.tool_params),
        e.frozen.fleet.id,
        e.frozen.fleet.version,
        risk_id,
        risk_ver,
        feat_id,
        feat_ver,
        e.proposer.strategy.value,
        e.proposer.allow_search_space_expansion,
        Jsonb(e.proposer.parameters),
        e.run.sandbox.value,
        e.run.runtime.value,
        e.run.sample_strategy,
        e.run.max_iterations,
        e.run.repetitions_per_case,
        e.run.parallelism,
        e.run.seed,
        e.run.persist_traces,
        e.run.convergence.min_delta,
        e.run.convergence.patience,
        e.run.convergence.early_stop,
        e.judge_defenses.holdout_visible_to_proposer,
        e.judge_defenses.overfit_penalty_max_delta,
        panel is not None,
        list(panel.members) if panel else [],
        panel.consensus_rule if panel else "majority",
        cf.enabled,
        cf.generation_strategy,
        cf.pairs_per_case,
        cf.max_score_variance,
        hsc.enabled,
        hsc.sample_rate,
        hsc.trigger_on_jump,
        adv_id,
        adv_ver,
        om is not None,
        list(om.metrics) if om else [],
        e.reliability.repetitions_per_case,
        list(e.reliability.metrics),
        e.decision.if_regression_fails,
        e.decision.if_guardrail_fails,
        e.decision.if_judge_human_disagree,
        e.error_analysis.enabled,
        e.error_analysis.taxonomy,
        e.error_analysis.trigger.when,
        e.error_analysis.trigger.threshold,
        e.error_analysis.scope,
    ]


def write_experiment_children(cur: Any, e: Experiment) -> None:
    """Replace the guardrail/dataset-gate/frozen-ref child rows for `e`."""
    cur.execute("DELETE FROM experiment_guardrails WHERE experiment_id = %s", (e.id,))
    for pos, g in enumerate(e.target.guardrails):
        cur.execute(
            "INSERT INTO experiment_guardrails "
            "(experiment_id, position, name, operator, value) VALUES (%s, %s, %s, %s, %s)",
            (e.id, pos, g.name, g.operator, g.value),
        )
    cur.execute("DELETE FROM experiment_dataset_gates WHERE experiment_id = %s", (e.id,))
    for pos, ref in enumerate(e.datasets.gates):
        cur.execute(
            "INSERT INTO experiment_dataset_gates "
            "(experiment_id, position, dataset_id, dataset_version) VALUES (%s, %s, %s, %s)",
            (e.id, pos, ref.id, ref.version),
        )
    cur.execute("DELETE FROM experiment_frozen_refs WHERE experiment_id = %s", (e.id,))
    for kind, refs in (
        ("agent", e.frozen.agents),
        ("tool", e.frozen.tools),
        ("dataset", e.frozen.datasets),
        ("grader", e.frozen.graders),
    ):
        for pos, ref in enumerate(refs):
            cur.execute(
                "INSERT INTO experiment_frozen_refs "
                "(experiment_id, ref_kind, position, ref_id, ref_version) "
                "VALUES (%s, %s, %s, %s, %s)",
                (e.id, kind, pos, ref.id, ref.version),
            )
