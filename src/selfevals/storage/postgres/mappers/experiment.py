"""Mapper for Experiment — the heaviest config entity.

Fixed-shape nested specs become flat prefixed columns; variable-length
EntityRef/MetricTarget lists become child tables; free-form parameter spaces
become JSONB columns. ``load`` reassembles the full nested Pydantic model.

Row-flattening/child-table writes and read-side reassembly are split into
`experiment_write.py`/`experiment_build.py` (same free-function-delegate
pattern as `trace_spans_write.py`/`trace_spans_read.py`) — this class stays
the single `EntityMapper` registered for `Experiment`, but its body is just
the SQL statement + delegate calls.
"""

from __future__ import annotations

from typing import Any

from selfevals.schemas.experiment import Experiment
from selfevals.storage.postgres.mappers.base import (
    SHARED_COLUMNS,
    EntityMapper,
    register_mapper,
    shared_values,
)
from selfevals.storage.postgres.mappers.experiment_build import build_experiment
from selfevals.storage.postgres.mappers.experiment_write import (
    experiment_row_values,
    write_experiment_children,
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


class ExperimentMapper(EntityMapper[Experiment]):
    entity_cls = Experiment
    table = "experiments"
    queryable_columns = frozenset({*SHARED_COLUMNS, "name", "state", "mode"})

    def upsert(self, cur: Any, entity: Experiment) -> None:
        values = experiment_row_values(shared_values(entity), entity)
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
        write_experiment_children(cur, entity)

    def load(self, cur: Any, workspace_id: str, entity_id: str) -> Experiment | None:
        cur.execute(
            f"SELECT {', '.join(_ALL_COLUMNS)} FROM {self.table} "
            "WHERE id = %s AND workspace_id = %s",
            (entity_id, workspace_id),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return build_experiment(cur, row, _ALL_COLUMNS)

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
        return [build_experiment(cur, row, _ALL_COLUMNS) for row in rows]


register_mapper(ExperimentMapper())
