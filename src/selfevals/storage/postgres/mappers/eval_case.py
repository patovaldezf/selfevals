"""Mapper for EvalCase — one atomic unit of evaluation.

EvalCase has no child tables: every list is a Postgres array (``TEXT[]``) and
every free-form payload (``input``/``context``/``failure_weights``/the JSON
``expected_*`` fields/``taxonomy_risk``) is a JSONB column. Fixed-shape nested
specs (CaseTaxonomy, Expected, Blocking, CaseMetadata) become flat prefixed
columns. ``load`` reassembles the full nested Pydantic model.
"""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from selfevals.schemas.eval_case import (
    Blocking,
    CaseMetadata,
    CaseTaxonomy,
    EvalCase,
    Expected,
    FeatureTag,
    GroundTruthSpec,
    SourceInfo,
)
from selfevals.schemas.registry import RiskProfile
from selfevals.storage.postgres.mappers.base import (
    SHARED_COLUMNS,
    EntityMapper,
    register_mapper,
    shared_values,
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
        e = entity
        tax = e.taxonomy
        exp = e.expected
        meta = e.metadata
        risk = tax.risk.model_dump(mode="json") if tax.risk is not None else None
        values = [
            *shared_values(e),
            e.experiment_id,
            e.name,
            e.task_type,
            [m.value for m in e.modalities],
            Jsonb(e.input),
            Jsonb(e.context),
            list(e.graders),
            Jsonb(e.failure_weights),
            list(e.critical_failure_modes),
            e.reference_output,
            e.holdout,
            e.content_hash,
            # CaseTaxonomy
            tax.level.value,
            tax.feature.primary,
            list(tax.feature.secondary),
            tax.source.type.value,
            tax.source.failure_type,
            tax.source.failure_id,
            tax.source.parent_case_id,
            [m.value for m in tax.ground_truth.methods],
            tax.runtime.value,
            tax.dataset_type.value,
            Jsonb(risk),
            # Expected
            exp.outcome,
            list(exp.must_include),
            exp.min_recall,
            list(exp.must_not_include),
            list(exp.required_tools),
            list(exp.forbidden_tools),
            list(exp.required_citations),
            list(exp.policy_flags),
            Jsonb(exp.structured_output),
            Jsonb(exp.output_schema),
            list(exp.required_sections),
            Jsonb(exp.aliases),
            # Blocking
            e.blocking.merge,
            e.blocking.release,
            # CaseMetadata
            meta.owner,
            list(meta.tags),
            meta.pii_status.value,
            meta.approved_raw_by,
            meta.approved_raw_at,
            meta.notes,
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

    def load(self, cur: Any, workspace_id: str, entity_id: str) -> EvalCase | None:
        cur.execute(
            f"SELECT {', '.join(_ALL_COLUMNS)} FROM {self.table} "
            "WHERE id = %s AND workspace_id = %s",
            (entity_id, workspace_id),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return self._build(row)

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
        return [self._build(row) for row in cur.fetchall()]

    def _build(self, row: tuple[Any, ...]) -> EvalCase:
        d = dict(zip(_ALL_COLUMNS, row, strict=True))
        risk = (
            RiskProfile.model_validate(d["taxonomy_risk"])
            if d["taxonomy_risk"] is not None
            else None
        )
        return EvalCase(
            id=d["id"],
            workspace_id=d["workspace_id"],
            version=d["version"],
            created_at=d["created_at"],
            updated_at=d["updated_at"],
            deleted_at=d["deleted_at"],
            experiment_id=d["experiment_id"],
            name=d["name"],
            task_type=d["task_type"],
            modalities=d["modalities"],
            input=d["input"],
            context=d["context"],
            expected=Expected(
                outcome=d["expected_outcome"],
                must_include=d["expected_must_include"],
                min_recall=d["expected_min_recall"],
                must_not_include=d["expected_must_not_include"],
                required_tools=d["expected_required_tools"],
                forbidden_tools=d["expected_forbidden_tools"],
                required_citations=d["expected_required_citations"],
                policy_flags=d["expected_policy_flags"],
                structured_output=d["expected_structured_output"],
                output_schema=d["expected_output_schema"],
                required_sections=d["expected_required_sections"],
                aliases=d["expected_aliases"],
            ),
            taxonomy=CaseTaxonomy(
                level=d["taxonomy_level"],
                feature=FeatureTag(
                    primary=d["taxonomy_feature_primary"],
                    secondary=d["taxonomy_feature_secondary"],
                ),
                source=SourceInfo(
                    type=d["taxonomy_source_type"],
                    failure_type=d["taxonomy_source_failure_type"],
                    failure_id=d["taxonomy_source_failure_id"],
                    parent_case_id=d["taxonomy_source_parent_case_id"],
                ),
                ground_truth=GroundTruthSpec(
                    methods=d["taxonomy_ground_truth_methods"],
                ),
                runtime=d["taxonomy_runtime"],
                dataset_type=d["taxonomy_dataset_type"],
                risk=risk,
            ),
            graders=d["graders"],
            failure_weights=d["failure_weights"],
            critical_failure_modes=d["critical_failure_modes"],
            reference_output=d["reference_output"],
            metadata=CaseMetadata(
                owner=d["metadata_owner"],
                tags=d["metadata_tags"],
                pii_status=d["metadata_pii_status"],
                approved_raw_by=d["metadata_approved_raw_by"],
                approved_raw_at=d["metadata_approved_raw_at"],
                notes=d["metadata_notes"],
            ),
            blocking=Blocking(
                merge=d["blocking_merge"],
                release=d["blocking_release"],
            ),
            holdout=d["holdout"],
            content_hash=d["content_hash"],
        )


register_mapper(EvalCaseMapper())
