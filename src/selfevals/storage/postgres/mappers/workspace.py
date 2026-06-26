"""Mappers for Workspace and Member."""

from __future__ import annotations

from typing import Any

from selfevals.schemas.workspace import Member, Workspace, WorkspaceSettings
from selfevals.storage.postgres.mappers.base import (
    SHARED_COLUMNS,
    EntityMapper,
    register_mapper,
    shared_values,
)

_WS_COLUMNS: tuple[str, ...] = (
    *SHARED_COLUMNS,
    "slug",
    "name",
    "description",
    "owner_id",
    "settings_default_runtime",
    "settings_retention_days",
)


class WorkspaceMapper(EntityMapper[Workspace]):
    entity_cls = Workspace
    table = "workspaces"
    queryable_columns = frozenset({*SHARED_COLUMNS, "slug", "name", "owner_id"})

    def upsert(self, cur: Any, entity: Workspace) -> None:
        values = [
            *shared_values(entity),
            entity.slug,
            entity.name,
            entity.description,
            entity.owner_id,
            entity.settings.default_runtime,
            entity.settings.retention_days,
        ]
        placeholders = ", ".join(["%s"] * len(_WS_COLUMNS))
        updates = ", ".join(
            f"{col} = EXCLUDED.{col}" for col in _WS_COLUMNS if col not in ("id", "created_at")
        )
        cur.execute(
            f"""
            INSERT INTO {self.table} ({", ".join(_WS_COLUMNS)})
            VALUES ({placeholders})
            ON CONFLICT (id) DO UPDATE SET {updates}
            """,
            values,
        )

    def load(self, cur: Any, workspace_id: str, entity_id: str) -> Workspace | None:
        cur.execute(
            f"SELECT {', '.join(_WS_COLUMNS)} FROM {self.table} "
            "WHERE id = %s AND workspace_id = %s",
            (entity_id, workspace_id),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return _row_to_workspace(row)

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
    ) -> list[Workspace]:
        self._validate_order_by(order_by)
        clauses, params = self._scalar_where_sql(where)
        clauses.insert(0, "workspace_id = %s")
        params.insert(0, workspace_id)
        sql = (
            f"SELECT {', '.join(_WS_COLUMNS)} FROM {self.table} "
            f"WHERE {' AND '.join(clauses)} "
            f"ORDER BY {order_by} {'DESC' if order_desc else 'ASC'}"
        )
        if limit is not None:
            sql += " LIMIT %s OFFSET %s"
            params.extend([limit, offset])
        cur.execute(sql, params)
        return [_row_to_workspace(row) for row in cur.fetchall()]


def _row_to_workspace(row: tuple[Any, ...]) -> Workspace:
    data = dict(zip(_WS_COLUMNS, row, strict=True))
    return Workspace(
        id=data["id"],
        workspace_id=data["workspace_id"],
        version=data["version"],
        created_at=data["created_at"],
        updated_at=data["updated_at"],
        deleted_at=data["deleted_at"],
        slug=data["slug"],
        name=data["name"],
        description=data["description"],
        owner_id=data["owner_id"],
        settings=WorkspaceSettings(
            default_runtime=data["settings_default_runtime"],
            retention_days=data["settings_retention_days"],
        ),
    )


_MEMBER_COLUMNS: tuple[str, ...] = (
    *SHARED_COLUMNS,
    "user_id",
    "role",
    "invited_by",
)


class MemberMapper(EntityMapper[Member]):
    entity_cls = Member
    table = "members"
    queryable_columns = frozenset({*SHARED_COLUMNS, "user_id", "role"})

    def upsert(self, cur: Any, entity: Member) -> None:
        values = [
            *shared_values(entity),
            entity.user_id,
            entity.role.value,
            entity.invited_by,
        ]
        placeholders = ", ".join(["%s"] * len(_MEMBER_COLUMNS))
        updates = ", ".join(
            f"{col} = EXCLUDED.{col}"
            for col in _MEMBER_COLUMNS
            if col not in ("id", "created_at")
        )
        cur.execute(
            f"""
            INSERT INTO {self.table} ({", ".join(_MEMBER_COLUMNS)})
            VALUES ({placeholders})
            ON CONFLICT (id) DO UPDATE SET {updates}
            """,
            values,
        )

    def load(self, cur: Any, workspace_id: str, entity_id: str) -> Member | None:
        cur.execute(
            f"SELECT {', '.join(_MEMBER_COLUMNS)} FROM {self.table} "
            "WHERE id = %s AND workspace_id = %s",
            (entity_id, workspace_id),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return _row_to_member(row)

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
    ) -> list[Member]:
        self._validate_order_by(order_by)
        clauses, params = self._scalar_where_sql(where)
        clauses.insert(0, "workspace_id = %s")
        params.insert(0, workspace_id)
        sql = (
            f"SELECT {', '.join(_MEMBER_COLUMNS)} FROM {self.table} "
            f"WHERE {' AND '.join(clauses)} "
            f"ORDER BY {order_by} {'DESC' if order_desc else 'ASC'}"
        )
        if limit is not None:
            sql += " LIMIT %s OFFSET %s"
            params.extend([limit, offset])
        cur.execute(sql, params)
        return [_row_to_member(row) for row in cur.fetchall()]


def _row_to_member(row: tuple[Any, ...]) -> Member:
    data = dict(zip(_MEMBER_COLUMNS, row, strict=True))
    return Member(
        id=data["id"],
        workspace_id=data["workspace_id"],
        version=data["version"],
        created_at=data["created_at"],
        updated_at=data["updated_at"],
        deleted_at=data["deleted_at"],
        user_id=data["user_id"],
        role=data["role"],
        invited_by=data["invited_by"],
    )


register_mapper(WorkspaceMapper())
register_mapper(MemberMapper())
