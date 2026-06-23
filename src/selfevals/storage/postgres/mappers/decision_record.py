"""Mapper for DecisionRecord — the per-candidate decision audit trail.

The DecisionRationale value object flattens to ``rationale_automated`` plus
the ``human_*`` columns (the human block is present iff ``human_decided_by``
is non-null). ``next_actions`` becomes the ``decision_next_actions`` child
table. ``metrics_snapshot`` is JSONB; ``affected_artifacts`` is a TEXT[].
"""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from selfevals.schemas.iteration import (
    DecisionRationale,
    DecisionRecord,
    HumanRationale,
    NextAction,
)
from selfevals.storage.postgres.mappers.base import (
    SHARED_COLUMNS,
    EntityMapper,
    register_mapper,
    shared_values,
)

_DECISION_COLUMNS: tuple[str, ...] = (
    *SHARED_COLUMNS,
    "experiment_id",
    "iteration",
    "variant_id",
    "outcome",
    "metrics_snapshot",
    "affected_artifacts",
    "superseded_by",
    "rationale_automated",
    "human_decided_by",
    "human_decided_at",
    "human_notes",
    "human_overrides_automated",
)


class DecisionRecordMapper(EntityMapper[DecisionRecord]):
    entity_cls = DecisionRecord
    table = "decision_records"
    queryable_columns = frozenset(
        {*SHARED_COLUMNS, "experiment_id", "outcome", "variant_id"}
    )

    def upsert(self, cur: Any, entity: DecisionRecord) -> None:
        e = entity
        human = e.rationale.human
        values = [
            *shared_values(e),
            e.experiment_id,
            e.iteration,
            e.variant_id,
            e.outcome.value,
            Jsonb(e.metrics_snapshot),
            list(e.affected_artifacts),
            e.superseded_by,
            e.rationale.automated,
            human.decided_by if human else None,
            human.decided_at if human else None,
            human.notes if human else None,
            human.overrides_automated if human else False,
        ]
        placeholders = ", ".join(["%s"] * len(_DECISION_COLUMNS))
        updates = ", ".join(
            f"{col} = EXCLUDED.{col}"
            for col in _DECISION_COLUMNS
            if col not in ("id", "created_at")
        )
        cur.execute(
            f"""
            INSERT INTO {self.table} ({", ".join(_DECISION_COLUMNS)})
            VALUES ({placeholders})
            ON CONFLICT (id) DO UPDATE SET {updates}
            """,
            values,
        )
        # Replace child rows (idempotent on update).
        cur.execute(
            "DELETE FROM decision_next_actions WHERE decision_record_id = %s", (e.id,)
        )
        for pos, action in enumerate(e.next_actions):
            cur.execute(
                "INSERT INTO decision_next_actions "
                "(decision_record_id, position, kind, payload) VALUES (%s, %s, %s, %s)",
                (e.id, pos, action.kind, Jsonb(action.payload)),
            )

    def load(self, cur: Any, workspace_id: str, entity_id: str) -> DecisionRecord | None:
        cur.execute(
            f"SELECT {', '.join(_DECISION_COLUMNS)} FROM {self.table} "
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
    ) -> list[DecisionRecord]:
        self._validate_order_by(order_by)
        clauses, params = self._scalar_where_sql(where)
        clauses.insert(0, "workspace_id = %s")
        params.insert(0, workspace_id)
        sql = (
            f"SELECT {', '.join(_DECISION_COLUMNS)} FROM {self.table} "
            f"WHERE {' AND '.join(clauses)} "
            f"ORDER BY {order_by} {'DESC' if order_desc else 'ASC'}"
        )
        if limit is not None:
            sql += " LIMIT %s OFFSET %s"
            params.extend([limit, offset])
        cur.execute(sql, params)
        return [self._build(cur, row) for row in cur.fetchall()]

    def _build(self, cur: Any, row: tuple[Any, ...]) -> DecisionRecord:
        d = dict(zip(_DECISION_COLUMNS, row, strict=True))
        cur.execute(
            "SELECT kind, payload FROM decision_next_actions "
            "WHERE decision_record_id = %s ORDER BY position",
            (d["id"],),
        )
        next_actions = [
            NextAction(kind=kind, payload=payload) for kind, payload in cur.fetchall()
        ]
        human = (
            HumanRationale(
                decided_by=d["human_decided_by"],
                decided_at=d["human_decided_at"],
                notes=d["human_notes"],
                overrides_automated=d["human_overrides_automated"],
            )
            if d["human_decided_by"] is not None
            else None
        )
        return DecisionRecord(
            id=d["id"],
            workspace_id=d["workspace_id"],
            version=d["version"],
            created_at=d["created_at"],
            updated_at=d["updated_at"],
            deleted_at=d["deleted_at"],
            experiment_id=d["experiment_id"],
            iteration=d["iteration"],
            variant_id=d["variant_id"],
            outcome=d["outcome"],
            rationale=DecisionRationale(
                automated=d["rationale_automated"],
                human=human,
            ),
            metrics_snapshot=d["metrics_snapshot"],
            affected_artifacts=d["affected_artifacts"],
            next_actions=next_actions,
            superseded_by=d["superseded_by"],
        )


register_mapper(DecisionRecordMapper())
