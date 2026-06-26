"""Mapper for FeatureRegistry — one declared feature path.

The fixed-shape ``default_risk`` (RiskProfile) becomes flat prefixed columns;
``failure_weight_defaults`` and ``parameters`` are free-form dicts stored as
JSONB. No child tables. ``load`` reassembles the nested Pydantic model.
"""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from selfevals.schemas.registry import FeatureRegistry, RiskProfile
from selfevals.storage.postgres.mappers.base import (
    SHARED_COLUMNS,
    EntityMapper,
    register_mapper,
    shared_values,
)

_EXTRA_COLUMNS: tuple[str, ...] = (
    "kind",
    "primary_feature",
    "owner",
    "description",
    "failure_weight_defaults",
    "parameters",
    "status",
    "replacement_feature_id",
    "risk_overall",
    "risk_user_trust",
    "risk_privacy",
    "risk_reversibility",
    "risk_safety",
    "risk_cost",
)
_ALL_COLUMNS: tuple[str, ...] = (*SHARED_COLUMNS, *_EXTRA_COLUMNS)


class FeatureRegistryMapper(EntityMapper[FeatureRegistry]):
    entity_cls = FeatureRegistry
    table = "feature_registries"
    queryable_columns = frozenset(
        {*SHARED_COLUMNS, "kind", "primary_feature", "status"}
    )

    def upsert(self, cur: Any, entity: FeatureRegistry) -> None:
        e = entity
        r = e.default_risk
        values = [
            *shared_values(e),
            e.kind.value,
            e.primary_feature,
            e.owner,
            e.description,
            Jsonb(e.failure_weight_defaults),
            Jsonb(e.parameters) if e.parameters is not None else None,
            e.status.value,
            e.replacement_feature_id,
            r.overall,
            r.user_trust,
            r.privacy,
            r.reversibility,
            r.safety,
            r.cost,
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

    def load(self, cur: Any, workspace_id: str, entity_id: str) -> FeatureRegistry | None:
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
    ) -> list[FeatureRegistry]:
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

    def _build(self, row: tuple[Any, ...]) -> FeatureRegistry:
        d = dict(zip(_ALL_COLUMNS, row, strict=True))
        return FeatureRegistry(
            id=d["id"],
            workspace_id=d["workspace_id"],
            version=d["version"],
            created_at=d["created_at"],
            updated_at=d["updated_at"],
            deleted_at=d["deleted_at"],
            kind=d["kind"],
            primary_feature=d["primary_feature"],
            owner=d["owner"],
            description=d["description"],
            default_risk=RiskProfile(
                overall=d["risk_overall"],
                user_trust=d["risk_user_trust"],
                privacy=d["risk_privacy"],
                reversibility=d["risk_reversibility"],
                safety=d["risk_safety"],
                cost=d["risk_cost"],
            ),
            failure_weight_defaults=d["failure_weight_defaults"],
            parameters=d["parameters"],
            status=d["status"],
            replacement_feature_id=d["replacement_feature_id"],
        )


register_mapper(FeatureRegistryMapper())
