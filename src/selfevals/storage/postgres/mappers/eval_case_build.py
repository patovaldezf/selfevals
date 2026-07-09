"""Free functions for `EvalCaseMapper` — row values + read-side reassembly.

Split out of `eval_case.py` (same free-function-delegate pattern as
`trace_spans_write.py`/`trace_spans_read.py`): pure functions of the entity
or the flattened row dict, no `self`.
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


def eval_case_row_values(shared: list[Any], e: EvalCase) -> list[Any]:
    """Flatten `e`'s nested specs into the `_ALL_COLUMNS`-ordered value list."""
    tax = e.taxonomy
    exp = e.expected
    meta = e.metadata
    risk = tax.risk.model_dump(mode="json") if tax.risk is not None else None
    return [
        *shared,
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


def build_eval_case(row: tuple[Any, ...], all_columns: tuple[str, ...]) -> EvalCase:
    d = dict(zip(all_columns, row, strict=True))
    risk = (
        RiskProfile.model_validate(d["taxonomy_risk"]) if d["taxonomy_risk"] is not None else None
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
