"""Free function for `ExperimentMapper._build` — reassembles a row into `Experiment`.

Split out of `experiment.py` (same free-function-delegate pattern as
`trace_spans_read.py`): pure functions of `cur` + the flattened row dict, no
`self`. `build_experiment` stays under the 120-line function-size threshold by
delegating the child-row reads and the `JudgeDefenses` sub-spec assembly to
private helpers below.
"""

from __future__ import annotations

from typing import Any

from selfevals.schemas._base import EntityRef
from selfevals.schemas.experiment import (
    AnalysisTriggerSpec,
    ConvergenceSpec,
    CounterfactualSpec,
    DatasetUsage,
    DecisionPolicy,
    EditableContract,
    ErrorAnalysisSpec,
    Experiment,
    ExperimentTaxonomy,
    FrozenSnapshot,
    HumanSpotCheckSpec,
    JudgeDefenses,
    JudgePanel,
    MetricTarget,
    OutcomeMetricsSpec,
    ProposerSpec,
    ReliabilitySpec,
    RunSpec,
    SearchSpace,
    TargetSpec,
)


def _load_experiment_children(
    cur: Any, eid: str
) -> tuple[list[MetricTarget], list[EntityRef], dict[str, list[EntityRef]]]:
    """Read the guardrail/dataset-gate/frozen-ref child rows for experiment `eid`."""
    cur.execute(
        "SELECT name, operator, value FROM experiment_guardrails "
        "WHERE experiment_id = %s ORDER BY position",
        (eid,),
    )
    guardrails = [MetricTarget(name=n, operator=op, value=v) for n, op, v in cur.fetchall()]
    cur.execute(
        "SELECT dataset_id, dataset_version FROM experiment_dataset_gates "
        "WHERE experiment_id = %s ORDER BY position",
        (eid,),
    )
    gates = [EntityRef(id=i, version=v) for i, v in cur.fetchall()]
    cur.execute(
        "SELECT ref_kind, ref_id, ref_version FROM experiment_frozen_refs "
        "WHERE experiment_id = %s ORDER BY ref_kind, position",
        (eid,),
    )
    frozen_by_kind: dict[str, list[EntityRef]] = {
        "agent": [],
        "tool": [],
        "dataset": [],
        "grader": [],
    }
    for kind, ref_id, ref_ver in cur.fetchall():
        frozen_by_kind[kind].append(EntityRef(id=ref_id, version=ref_ver))
    return guardrails, gates, frozen_by_kind


def _build_judge_defenses(d: dict[str, Any]) -> JudgeDefenses:
    panel = (
        JudgePanel(members=d["jd_panel_members"], consensus_rule=d["jd_panel_consensus_rule"])
        if d["jd_panel_present"]
        else None
    )
    outcome_metrics = (
        OutcomeMetricsSpec(metrics=d["jd_outcome_metrics"])
        if d["jd_outcome_metrics_present"]
        else None
    )
    adv = (
        EntityRef(id=d["jd_adversarial_dataset_id"], version=d["jd_adversarial_dataset_version"])
        if d["jd_adversarial_dataset_id"] is not None
        else None
    )
    return JudgeDefenses(
        holdout_visible_to_proposer=d["jd_holdout_visible_to_proposer"],
        overfit_penalty_max_delta=d["jd_overfit_penalty_max_delta"],
        panel=panel,
        counterfactuals=CounterfactualSpec(
            enabled=d["jd_cf_enabled"],
            generation_strategy=d["jd_cf_generation_strategy"],
            pairs_per_case=d["jd_cf_pairs_per_case"],
            max_score_variance=d["jd_cf_max_score_variance"],
        ),
        human_spot_check=HumanSpotCheckSpec(
            enabled=d["jd_hsc_enabled"],
            sample_rate=d["jd_hsc_sample_rate"],
            trigger_on_jump=d["jd_hsc_trigger_on_jump"],
        ),
        adversarial_dataset=adv,
        outcome_metrics=outcome_metrics,
    )


def _build_frozen(
    d: dict[str, Any], frozen_by_kind: dict[str, list[EntityRef]]
) -> FrozenSnapshot:
    risk = (
        EntityRef(id=d["frozen_risk_registry_id"], version=d["frozen_risk_registry_version"])
        if d["frozen_risk_registry_id"] is not None
        else None
    )
    feat = (
        EntityRef(id=d["frozen_feature_registry_id"], version=d["frozen_feature_registry_version"])
        if d["frozen_feature_registry_id"] is not None
        else None
    )
    return FrozenSnapshot(
        fleet=EntityRef(id=d["frozen_fleet_id"], version=d["frozen_fleet_version"]),
        agents=frozen_by_kind["agent"],
        tools=frozen_by_kind["tool"],
        datasets=frozen_by_kind["dataset"],
        graders=frozen_by_kind["grader"],
        risk_registry=risk,
        feature_registry=feat,
    )


def build_experiment(cur: Any, row: tuple[Any, ...], all_columns: tuple[str, ...]) -> Experiment:
    d = dict(zip(all_columns, row, strict=True))
    guardrails, gates, frozen_by_kind = _load_experiment_children(cur, d["id"])

    return Experiment(
        id=d["id"],
        workspace_id=d["workspace_id"],
        version=d["version"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
        deleted_at=d["deleted_at"],
        name=d["name"],
        goal=d["goal"],
        mode=d["mode"],
        state=d["state"],
        content_hash=d["content_hash"],
        taxonomy=ExperimentTaxonomy(
            target_features=d["taxonomy_target_features"],
            target_levels=d["taxonomy_target_levels"],
            dataset_types=d["taxonomy_dataset_types"],
        ),
        datasets=DatasetUsage(
            optimization=EntityRef(
                id=d["dataset_optimization_id"], version=d["dataset_optimization_version"]
            ),
            gates=gates,
        ),
        target=TargetSpec(
            primary=MetricTarget(
                name=d["target_primary_name"],
                operator=d["target_primary_operator"],
                value=d["target_primary_value"],
            ),
            guardrails=guardrails,
            primary_grader=d["target_primary_grader"],
        ),
        editable=EditableContract(
            prompt=d["editable_prompt"],
            model_params=d["editable_model_params"],
            model_choice=d["editable_model_choice"],
            tool_descriptions=d["editable_tool_descriptions"],
            tool_code=d["editable_tool_code"],
            workflow_graph=d["editable_workflow_graph"],
            skills=d["editable_skills"],
            dataset=d["editable_dataset"],
            graders=d["editable_graders"],
        ),
        search_space=SearchSpace(
            model_params=d["search_space_model_params"],
            prompt_variables=d["search_space_prompt_variables"],
            tool_params=d["search_space_tool_params"],
        ),
        frozen=_build_frozen(d, frozen_by_kind),
        proposer=ProposerSpec(
            strategy=d["proposer_strategy"],
            allow_search_space_expansion=d["proposer_allow_search_space_expansion"],
            parameters=d["proposer_parameters"],
        ),
        run=RunSpec(
            sandbox=d["run_sandbox"],
            runtime=d["run_runtime"],
            sample_strategy=d["run_sample_strategy"],
            max_iterations=d["run_max_iterations"],
            repetitions_per_case=d["run_repetitions_per_case"],
            parallelism=d["run_parallelism"],
            seed=d["run_seed"],
            persist_traces=d["run_persist_traces"],
            convergence=ConvergenceSpec(
                min_delta=d["run_convergence_min_delta"],
                patience=d["run_convergence_patience"],
                early_stop=d["run_convergence_early_stop"],
            ),
        ),
        judge_defenses=_build_judge_defenses(d),
        reliability=ReliabilitySpec(
            repetitions_per_case=d["reliability_repetitions_per_case"],
            metrics=d["reliability_metrics"],
        ),
        decision=DecisionPolicy(
            if_regression_fails=d["decision_if_regression_fails"],
            if_guardrail_fails=d["decision_if_guardrail_fails"],
            if_judge_human_disagree=d["decision_if_judge_human_disagree"],
        ),
        error_analysis=ErrorAnalysisSpec(
            enabled=d["ea_enabled"],
            taxonomy=d["ea_taxonomy"],
            trigger=AnalysisTriggerSpec(
                when=d["ea_trigger_when"], threshold=d["ea_trigger_threshold"]
            ),
            scope=d["ea_scope"],
        ),
    )
