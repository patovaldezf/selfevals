"""Mapper for PairwiseVerdict — an A-vs-B head-to-head judgement.

The two nested ``PairRef`` sides flatten to ``a_*`` / ``b_*`` columns; every
other field is a flat scalar column (no JSONB, no child tables). The cross-
entity ids (experiment_id / case_id / trace_ids) are loose columns, not FKs —
existence is enforced in the ingest layer. ``load`` reassembles both PairRefs.
"""

from __future__ import annotations

from typing import Any

from selfevals.schemas.pairwise_verdict import PairRef, PairwiseVerdict
from selfevals.storage.postgres.mappers.base import (
    SHARED_COLUMNS,
    EntityMapper,
    register_mapper,
    shared_values,
)

_EXTRA_COLUMNS: tuple[str, ...] = (
    "a_kind",
    "a_trace_id",
    "a_case_id",
    "a_iteration_id",
    "a_content_snapshot",
    "b_kind",
    "b_trace_id",
    "b_case_id",
    "b_iteration_id",
    "b_content_snapshot",
    "preferred",
    "margin",
    "rationale",
    "judge_kind",
    "judge_id",
    "judge_model",
    "rubric_version",
    "position",
    "experiment_id",
    "case_id",
    "dataset_id",
    "submitted_at",
    "duration_seconds",
)
_ALL_COLUMNS: tuple[str, ...] = (*SHARED_COLUMNS, *_EXTRA_COLUMNS)


class PairwiseVerdictMapper(EntityMapper[PairwiseVerdict]):
    entity_cls = PairwiseVerdict
    table = "pairwise_verdicts"
    queryable_columns = frozenset(
        {*SHARED_COLUMNS, "experiment_id", "case_id", "judge_kind"}
    )

    def upsert(self, cur: Any, entity: PairwiseVerdict) -> None:
        e = entity
        values = [
            *shared_values(e),
            e.a_ref.kind,
            e.a_ref.trace_id,
            e.a_ref.case_id,
            e.a_ref.iteration_id,
            e.a_ref.content_snapshot,
            e.b_ref.kind,
            e.b_ref.trace_id,
            e.b_ref.case_id,
            e.b_ref.iteration_id,
            e.b_ref.content_snapshot,
            e.preferred,
            e.margin,
            e.rationale,
            e.judge_kind,
            e.judge_id,
            e.judge_model,
            e.rubric_version,
            e.position,
            e.experiment_id,
            e.case_id,
            e.dataset_id,
            e.submitted_at,
            e.duration_seconds,
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

    def load(self, cur: Any, workspace_id: str, entity_id: str) -> PairwiseVerdict | None:
        cur.execute(
            f"SELECT {', '.join(_ALL_COLUMNS)} FROM {self.table} "
            "WHERE id = %s AND workspace_id = %s",
            (entity_id, workspace_id),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return _row_to_verdict(row)

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
    ) -> list[PairwiseVerdict]:
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
        return [_row_to_verdict(row) for row in cur.fetchall()]


def _row_to_verdict(row: tuple[Any, ...]) -> PairwiseVerdict:
    d = dict(zip(_ALL_COLUMNS, row, strict=True))
    return PairwiseVerdict(
        id=d["id"],
        workspace_id=d["workspace_id"],
        version=d["version"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
        deleted_at=d["deleted_at"],
        a_ref=PairRef(
            kind=d["a_kind"],
            trace_id=d["a_trace_id"],
            case_id=d["a_case_id"],
            iteration_id=d["a_iteration_id"],
            content_snapshot=d["a_content_snapshot"],
        ),
        b_ref=PairRef(
            kind=d["b_kind"],
            trace_id=d["b_trace_id"],
            case_id=d["b_case_id"],
            iteration_id=d["b_iteration_id"],
            content_snapshot=d["b_content_snapshot"],
        ),
        preferred=d["preferred"],
        margin=d["margin"],
        rationale=d["rationale"],
        judge_kind=d["judge_kind"],
        judge_id=d["judge_id"],
        judge_model=d["judge_model"],
        rubric_version=d["rubric_version"],
        position=d["position"],
        experiment_id=d["experiment_id"],
        case_id=d["case_id"],
        dataset_id=d["dataset_id"],
        submitted_at=d["submitted_at"],
        duration_seconds=d["duration_seconds"],
    )


register_mapper(PairwiseVerdictMapper())
