"""Mapper for Annotation — a human judgement on a case+trace pair.

Scalar fields become flat columns; the nested ``AnnotationLabels`` flattens to
``labels_rubric_version`` plus a ``labels_data`` JSONB column. No child tables.
``load`` reassembles the full nested Pydantic model.
"""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from selfevals.schemas.annotation import Annotation, AnnotationLabels
from selfevals.storage.postgres.mappers.base import (
    SHARED_COLUMNS,
    EntityMapper,
    register_mapper,
    shared_values,
)

_EXTRA_COLUMNS: tuple[str, ...] = (
    "case_id",
    "trace_id",
    "annotator_id",
    "labels_rubric_version",
    "labels_data",
    "notes",
    "confidence",
    "flagged_for_adjudication",
    "started_at",
    "submitted_at",
    "duration_seconds",
)
_ALL_COLUMNS: tuple[str, ...] = (*SHARED_COLUMNS, *_EXTRA_COLUMNS)


class AnnotationMapper(EntityMapper[Annotation]):
    entity_cls = Annotation
    table = "annotations"
    queryable_columns = frozenset({*SHARED_COLUMNS, "case_id", "annotator_id"})

    def upsert(self, cur: Any, entity: Annotation) -> None:
        e = entity
        values = [
            *shared_values(e),
            e.case_id,
            e.trace_id,
            e.annotator_id,
            e.labels.rubric_version,
            Jsonb(e.labels.data),
            e.notes,
            e.confidence,
            e.flagged_for_adjudication,
            e.started_at,
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

    def load(self, cur: Any, workspace_id: str, entity_id: str) -> Annotation | None:
        cur.execute(
            f"SELECT {', '.join(_ALL_COLUMNS)} FROM {self.table} "
            "WHERE id = %s AND workspace_id = %s",
            (entity_id, workspace_id),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return _row_to_annotation(row)

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
    ) -> list[Annotation]:
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
        return [_row_to_annotation(row) for row in cur.fetchall()]


def _row_to_annotation(row: tuple[Any, ...]) -> Annotation:
    d = dict(zip(_ALL_COLUMNS, row, strict=True))
    return Annotation(
        id=d["id"],
        workspace_id=d["workspace_id"],
        version=d["version"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
        deleted_at=d["deleted_at"],
        case_id=d["case_id"],
        trace_id=d["trace_id"],
        annotator_id=d["annotator_id"],
        labels=AnnotationLabels(
            rubric_version=d["labels_rubric_version"],
            data=d["labels_data"],
        ),
        notes=d["notes"],
        confidence=d["confidence"],
        flagged_for_adjudication=d["flagged_for_adjudication"],
        started_at=d["started_at"],
        submitted_at=d["submitted_at"],
        duration_seconds=d["duration_seconds"],
    )


register_mapper(AnnotationMapper())
