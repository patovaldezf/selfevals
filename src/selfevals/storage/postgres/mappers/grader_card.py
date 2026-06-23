"""Mapper for GraderCard — a grader's calibration passport.

Fixed-shape nested specs (io, human_reference, metrics, thresholds,
degrade_behavior) become flat prefixed columns. No child tables, no JSONB.
``load`` reassembles the full nested Pydantic model.
"""

from __future__ import annotations

from typing import Any

from selfevals.schemas.grader_card import (
    CalibrationMetrics,
    CalibrationThresholds,
    DegradeBehavior,
    GraderCard,
    GraderIO,
    HumanReference,
)
from selfevals.storage.postgres.mappers.base import (
    SHARED_COLUMNS,
    EntityMapper,
    register_mapper,
    shared_values,
)

_EXTRA_COLUMNS: tuple[str, ...] = (
    "name",
    "purpose",
    "grader_kind",
    "method",
    "blocking",
    "review_cadence",
    "state",
    "io_input_fields",
    "io_output_label_set",
    "io_output_kind",
    "href_dataset_id",
    "href_annotator_count",
    "href_adjudication",
    "metrics_precision",
    "metrics_recall",
    "metrics_f1",
    "metrics_macro_f1",
    "metrics_spearman",
    "metrics_mae",
    "metrics_pairwise_agreement",
    "metrics_high_risk_false_negatives",
    "metrics_human_human_agreement",
    "thr_min_precision",
    "thr_min_recall",
    "thr_min_f1",
    "thr_max_high_risk_false_negatives",
    "degrade_on_threshold_breach",
    "degrade_alert_channels",
)
_ALL_COLUMNS: tuple[str, ...] = (*SHARED_COLUMNS, *_EXTRA_COLUMNS)


class GraderCardMapper(EntityMapper[GraderCard]):
    entity_cls = GraderCard
    table = "grader_cards"
    queryable_columns = frozenset(
        {*SHARED_COLUMNS, "name", "state", "method", "blocking"}
    )

    def upsert(self, cur: Any, entity: GraderCard) -> None:
        e = entity
        values = [
            *shared_values(e),
            e.name,
            e.purpose,
            e.grader_kind,
            e.method.value,
            e.blocking,
            e.review_cadence,
            e.state.value,
            list(e.io.input_fields),
            list(e.io.output_label_set),
            e.io.output_kind,
            e.human_reference.dataset_id,
            e.human_reference.annotator_count,
            e.human_reference.adjudication,
            e.metrics.precision,
            e.metrics.recall,
            e.metrics.f1,
            e.metrics.macro_f1,
            e.metrics.spearman,
            e.metrics.mae,
            e.metrics.pairwise_agreement,
            e.metrics.high_risk_false_negatives,
            e.metrics.human_human_agreement,
            e.thresholds.min_precision,
            e.thresholds.min_recall,
            e.thresholds.min_f1,
            e.thresholds.max_high_risk_false_negatives,
            e.degrade_behavior.on_threshold_breach,
            list(e.degrade_behavior.alert_channels),
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

    def load(self, cur: Any, workspace_id: str, entity_id: str) -> GraderCard | None:
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
    ) -> list[GraderCard]:
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

    def _build(self, row: tuple[Any, ...]) -> GraderCard:
        d = dict(zip(_ALL_COLUMNS, row, strict=True))
        return GraderCard(
            id=d["id"],
            workspace_id=d["workspace_id"],
            version=d["version"],
            created_at=d["created_at"],
            updated_at=d["updated_at"],
            deleted_at=d["deleted_at"],
            name=d["name"],
            purpose=d["purpose"],
            grader_kind=d["grader_kind"],
            method=d["method"],
            blocking=d["blocking"],
            io=GraderIO(
                input_fields=d["io_input_fields"],
                output_label_set=d["io_output_label_set"],
                output_kind=d["io_output_kind"],
            ),
            human_reference=HumanReference(
                dataset_id=d["href_dataset_id"],
                annotator_count=d["href_annotator_count"],
                adjudication=d["href_adjudication"],
            ),
            metrics=CalibrationMetrics(
                precision=d["metrics_precision"],
                recall=d["metrics_recall"],
                f1=d["metrics_f1"],
                macro_f1=d["metrics_macro_f1"],
                spearman=d["metrics_spearman"],
                mae=d["metrics_mae"],
                pairwise_agreement=d["metrics_pairwise_agreement"],
                high_risk_false_negatives=d["metrics_high_risk_false_negatives"],
                human_human_agreement=d["metrics_human_human_agreement"],
            ),
            thresholds=CalibrationThresholds(
                min_precision=d["thr_min_precision"],
                min_recall=d["thr_min_recall"],
                min_f1=d["thr_min_f1"],
                max_high_risk_false_negatives=d["thr_max_high_risk_false_negatives"],
            ),
            review_cadence=d["review_cadence"],
            degrade_behavior=DegradeBehavior(
                on_threshold_breach=d["degrade_on_threshold_breach"],
                alert_channels=d["degrade_alert_channels"],
            ),
            state=d["state"],
        )


register_mapper(GraderCardMapper())
