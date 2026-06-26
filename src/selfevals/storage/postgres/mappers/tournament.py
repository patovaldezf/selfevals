"""Mapper for Tournament — the ranking from a batch of pairwise comparisons.

Scalars become flat columns; ``candidate_ids`` is a ``TEXT[]`` array; the
variable-length ``ranking`` (``TournamentRow`` list) becomes child rows in
``tournament_rows`` (DELETE+INSERT on upsert, ordered SELECT on load — the
``experiment_guardrails`` pattern). ``experiment_id`` is a loose reference.
"""

from __future__ import annotations

from typing import Any

from selfevals.schemas.tournament import Tournament, TournamentRow
from selfevals.storage.postgres.mappers.base import (
    SHARED_COLUMNS,
    EntityMapper,
    register_mapper,
    shared_values,
)

_EXTRA_COLUMNS: tuple[str, ...] = (
    "experiment_id",
    "strategy",
    "method",
    "candidate_ids",
    "baseline_id",
    "n_comparisons",
    "swap_and_average",
)
_ALL_COLUMNS: tuple[str, ...] = (*SHARED_COLUMNS, *_EXTRA_COLUMNS)


class TournamentMapper(EntityMapper[Tournament]):
    entity_cls = Tournament
    table = "tournaments"
    queryable_columns = frozenset({*SHARED_COLUMNS, "experiment_id"})

    def upsert(self, cur: Any, entity: Tournament) -> None:
        e = entity
        values = [
            *shared_values(e),
            e.experiment_id,
            e.strategy,
            e.method,
            list(e.candidate_ids),
            e.baseline_id,
            e.n_comparisons,
            e.swap_and_average,
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
        # Replace child rows (idempotent on update).
        cur.execute("DELETE FROM tournament_rows WHERE tournament_id = %s", (e.id,))
        for pos, r in enumerate(e.ranking):
            cur.execute(
                "INSERT INTO tournament_rows "
                "(tournament_id, position, candidate_id, rank, score, "
                "wins, losses, ties, n_comparisons) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    e.id,
                    pos,
                    r.candidate_id,
                    r.rank,
                    r.score,
                    r.wins,
                    r.losses,
                    r.ties,
                    r.n_comparisons,
                ),
            )

    def load(self, cur: Any, workspace_id: str, entity_id: str) -> Tournament | None:
        cur.execute(
            f"SELECT {', '.join(_ALL_COLUMNS)} FROM {self.table} "
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
    ) -> list[Tournament]:
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
        return [self._build(cur, row) for row in rows]

    def _build(self, cur: Any, row: tuple[Any, ...]) -> Tournament:
        d = dict(zip(_ALL_COLUMNS, row, strict=True))
        cur.execute(
            "SELECT candidate_id, rank, score, wins, losses, ties, n_comparisons "
            "FROM tournament_rows WHERE tournament_id = %s ORDER BY position",
            (d["id"],),
        )
        ranking = [
            TournamentRow(
                candidate_id=cid,
                rank=rank,
                score=score,
                wins=wins,
                losses=losses,
                ties=ties,
                n_comparisons=ncmp,
            )
            for cid, rank, score, wins, losses, ties, ncmp in cur.fetchall()
        ]
        return Tournament(
            id=d["id"],
            workspace_id=d["workspace_id"],
            version=d["version"],
            created_at=d["created_at"],
            updated_at=d["updated_at"],
            deleted_at=d["deleted_at"],
            experiment_id=d["experiment_id"],
            strategy=d["strategy"],
            method=d["method"],
            candidate_ids=list(d["candidate_ids"]),
            baseline_id=d["baseline_id"],
            n_comparisons=d["n_comparisons"],
            swap_and_average=d["swap_and_average"],
            ranking=ranking,
        )


register_mapper(TournamentMapper())
