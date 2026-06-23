"""Mapper for Experiment — the heaviest config entity.

Fixed-shape nested specs become flat prefixed columns; variable-length
EntityRef/MetricTarget lists become child tables; free-form parameter spaces
become JSONB columns. ``load`` reassembles the full nested Pydantic model.
"""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

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
from selfevals.storage.postgres.mappers.base import (
    SHARED_COLUMNS,
    EntityMapper,
    register_mapper,
    shared_values,
)

# Main-table columns after the shared ones, in insert order.
_EXTRA_COLUMNS: tuple[str, ...] = (
    "name",
    "goal",
    "mode",
    "state",
    "content_hash",
    "taxonomy_target_features",
    "taxonomy_target_levels",
    "taxonomy_dataset_types",
    "dataset_optimization_id",
    "dataset_optimization_version",
    "target_primary_name",
    "target_primary_operator",
    "target_primary_value",
    "target_primary_grader",
    "editable_prompt",
    "editable_model_params",
    "editable_model_choice",
    "editable_tool_descriptions",
    "editable_tool_code",
    "editable_workflow_graph",
    "editable_skills",
    "editable_dataset",
    "editable_graders",
    "search_space_model_params",
    "search_space_prompt_variables",
    "search_space_tool_params",
    "frozen_fleet_id",
    "frozen_fleet_version",
    "frozen_risk_registry_id",
    "frozen_risk_registry_version",
    "frozen_feature_registry_id",
    "frozen_feature_registry_version",
    "proposer_strategy",
    "proposer_allow_search_space_expansion",
    "proposer_parameters",
    "run_sandbox",
    "run_runtime",
    "run_sample_strategy",
    "run_max_iterations",
    "run_repetitions_per_case",
    "run_parallelism",
    "run_seed",
    "run_persist_traces",
    "run_convergence_min_delta",
    "run_convergence_patience",
    "run_convergence_early_stop",
    "jd_holdout_visible_to_proposer",
    "jd_overfit_penalty_max_delta",
    "jd_panel_present",
    "jd_panel_members",
    "jd_panel_consensus_rule",
    "jd_cf_enabled",
    "jd_cf_generation_strategy",
    "jd_cf_pairs_per_case",
    "jd_cf_max_score_variance",
    "jd_hsc_enabled",
    "jd_hsc_sample_rate",
    "jd_hsc_trigger_on_jump",
    "jd_adversarial_dataset_id",
    "jd_adversarial_dataset_version",
    "jd_outcome_metrics_present",
    "jd_outcome_metrics",
    "reliability_repetitions_per_case",
    "reliability_metrics",
    "decision_if_regression_fails",
    "decision_if_guardrail_fails",
    "decision_if_judge_human_disagree",
    "ea_enabled",
    "ea_taxonomy",
    "ea_trigger_when",
    "ea_trigger_threshold",
    "ea_scope",
)
_ALL_COLUMNS: tuple[str, ...] = (*SHARED_COLUMNS, *_EXTRA_COLUMNS)


def _ref_pair(ref: EntityRef | None) -> tuple[str | None, int | None]:
    if ref is None:
        return None, None
    return ref.id, ref.version


class ExperimentMapper(EntityMapper[Experiment]):
    entity_cls = Experiment
    table = "experiments"
    queryable_columns = frozenset({*SHARED_COLUMNS, "name", "state", "mode"})

    def upsert(self, cur: Any, entity: Experiment) -> None:
        e = entity
        risk_id, risk_ver = _ref_pair(e.frozen.risk_registry)
        feat_id, feat_ver = _ref_pair(e.frozen.feature_registry)
        adv_id, adv_ver = _ref_pair(e.judge_defenses.adversarial_dataset)
        panel = e.judge_defenses.panel
        cf = e.judge_defenses.counterfactuals
        hsc = e.judge_defenses.human_spot_check
        om = e.judge_defenses.outcome_metrics
        values = [
            *shared_values(e),
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
        placeholders = ", ".join(["%s"] * len(_ALL_COLUMNS))
        updates = ", ".join(
            f"{c} = EXCLUDED.{c}" for c in _ALL_COLUMNS if c not in ("id", "created_at")
        )
        cur.execute(
            f"""
            INSERT INTO {self.table} ({", ".join(_ALL_COLUMNS)})
            VALUES ({placeholders})
            ON CONFLICT (id) DO UPDATE SET {updates}
            """,
            values,
        )
        # Replace child rows (idempotent on update).
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

    def load(self, cur: Any, workspace_id: str, entity_id: str) -> Experiment | None:
        cur.execute(
            f"SELECT {', '.join(_ALL_COLUMNS)} FROM {self.table} "
            "WHERE id = %s AND workspace_id = %s",
            (entity_id, workspace_id),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return self._build(cur, row)

    def load_many(
        self,
        cur: Any,
        *,
        workspace_id: str,
        where: dict[str, Any],
        order_by: str,
        order_desc: bool,
        limit: int | None,
        offset: int,
    ) -> list[Experiment]:
        self._validate_order_by(order_by)
        clauses, params = self._scalar_where_sql(where)
        clauses.insert(0, "workspace_id = %s")
        params.insert(0, workspace_id)
        sql = (
            f"SELECT {', '.join(_ALL_COLUMNS)} FROM {self.table} "
            f"WHERE {' AND '.join(clauses)} "
            f"ORDER BY {order_by} {'DESC' if order_desc else 'ASC'}"
        )
        if limit is not None:
            sql += " LIMIT %s OFFSET %s"
            params.extend([limit, offset])
        cur.execute(sql, params)
        rows = cur.fetchall()
        return [self._build(cur, row) for row in rows]

    def _build(self, cur: Any, row: tuple[Any, ...]) -> Experiment:
        d = dict(zip(_ALL_COLUMNS, row, strict=True))
        eid = d["id"]
        # Child rows.
        cur.execute(
            "SELECT name, operator, value FROM experiment_guardrails "
            "WHERE experiment_id = %s ORDER BY position",
            (eid,),
        )
        guardrails = [
            MetricTarget(name=n, operator=op, value=v) for n, op, v in cur.fetchall()
        ]
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

        panel = (
            JudgePanel(
                members=d["jd_panel_members"],
                consensus_rule=d["jd_panel_consensus_rule"],
            )
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
        risk = (
            EntityRef(id=d["frozen_risk_registry_id"], version=d["frozen_risk_registry_version"])
            if d["frozen_risk_registry_id"] is not None
            else None
        )
        feat = (
            EntityRef(
                id=d["frozen_feature_registry_id"], version=d["frozen_feature_registry_version"]
            )
            if d["frozen_feature_registry_id"] is not None
            else None
        )
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
            frozen=FrozenSnapshot(
                fleet=EntityRef(id=d["frozen_fleet_id"], version=d["frozen_fleet_version"]),
                agents=frozen_by_kind["agent"],
                tools=frozen_by_kind["tool"],
                datasets=frozen_by_kind["dataset"],
                graders=frozen_by_kind["grader"],
                risk_registry=risk,
                feature_registry=feat,
            ),
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
            judge_defenses=JudgeDefenses(
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
            ),
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


register_mapper(ExperimentMapper())
