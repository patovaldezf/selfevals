"""Mapper for EvalCase — one atomic unit of evaluation.

EvalCase has no child tables: every list is a Postgres array (``TEXT[]``) and
every free-form payload (``input``/``context``/``failure_weights``/the JSON
``expected_*`` fields/``taxonomy_risk``) is a JSONB column. Fixed-shape nested
specs (CaseTaxonomy, Expected, Blocking, CaseMetadata) become flat prefixed
columns. ``load`` reassembles the full nested Pydantic model.

Row-flattening and read-side reassembly are split into `eval_case_build.py`
(same free-function-delegate pattern as `trace_spans_write.py`/
`trace_spans_read.py`) — this class stays the single `EntityMapper`
registered for `EvalCase`, but its body is just the SQL statement + delegate
calls.
"""

from __future__ import annotations

from typing import Any

from selfevals.schemas.eval_case import EvalCase
from selfevals.storage.postgres.mappers.base import (
    SHARED_COLUMNS,
    EntityMapper,
    register_mapper,
    shared_values,
)
from selfevals.storage.postgres.mappers.eval_case_build import (
    build_eval_case,
    eval_case_row_values,
)

# Main-table columns after the shared ones, in DDL/insert order.
_EXTRA_COLUMNS: tuple[str, ...] = (
    "experiment_id",
    "name",
    "task_type",
    "modalities",
    "input",
    "context",
    "graders",
    "failure_weights",
    "critical_failure_modes",
    "reference_output",
    "holdout",
    "content_hash",
    # CaseTaxonomy
    "taxonomy_level",
    "taxonomy_feature_primary",
    "taxonomy_feature_secondary",
    "taxonomy_source_type",
    "taxonomy_source_failure_type",
    "taxonomy_source_failure_id",
    "taxonomy_source_parent_case_id",
    "taxonomy_ground_truth_methods",
    "taxonomy_runtime",
    "taxonomy_dataset_type",
    "taxonomy_risk",
    # Expected
    "expected_outcome",
    "expected_must_include",
    "expected_min_recall",
    "expected_must_not_include",
    "expected_required_tools",
    "expected_forbidden_tools",
    "expected_required_citations",
    "expected_policy_flags",
    "expected_structured_output",
    "expected_output_schema",
    "expected_required_sections",
    "expected_aliases",
    # Blocking
    "blocking_merge",
    "blocking_release",
    # CaseMetadata
    "metadata_owner",
    "metadata_tags",
    "metadata_pii_status",
    "metadata_approved_raw_by",
    "metadata_approved_raw_at",
    "metadata_notes",
)
_ALL_COLUMNS: tuple[str, ...] = (*SHARED_COLUMNS, *_EXTRA_COLUMNS)


class EvalCaseMapper(EntityMapper[EvalCase]):
    entity_cls = EvalCase
    table = "eval_cases"
    queryable_columns = frozenset(
        {*SHARED_COLUMNS, "experiment_id", "name", "task_type", "holdout"}
    )

    def upsert(self, cur: Any, entity: EvalCase) -> None:
        values = eval_case_row_values(shared_values(entity), entity)
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

    def load(self, cur: Any, workspace_id: str, entity_id: str) -> EvalCase | None:
        cur.execute(
            f"SELECT {', '.join(_ALL_COLUMNS)} FROM {self.table} "
            "WHERE id = %s AND workspace_id = %s",
            (entity_id, workspace_id),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return build_eval_case(row, _ALL_COLUMNS)

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
    ) -> list[EvalCase]:
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
        return [build_eval_case(row, _ALL_COLUMNS) for row in cur.fetchall()]


register_mapper(EvalCaseMapper())
