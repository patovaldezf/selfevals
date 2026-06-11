"""Postgres implementation of StorageInterface.

This backend keeps the generic entity contract for compatibility while
projecting hot entities into relational tables with queryable columns.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Self

from selfevals._internal.time import utc_now
from selfevals.storage.errors import (
    EntityNotFoundError,
    OptimisticConcurrencyError,
    WorkspaceMismatchError,
)
from selfevals.storage.interface import ListFilter, StorageInterface, WorkspaceScope

if TYPE_CHECKING:
    from selfevals.api.schemas import WorkspaceSummary
    from selfevals.schemas._base import BaseEntity
    from selfevals.schemas.eval_case import EvalCase
    from selfevals.schemas.experiment import Experiment
    from selfevals.schemas.trace import Trace


_ORDERABLE_COLUMNS = {
    "entity_type",
    "id",
    "workspace_id",
    "version",
    "created_at",
    "updated_at",
    "deleted_at",
}


def _entity_type_name(cls: type[BaseEntity]) -> str:
    return cls.__name__


class PostgresStorage(StorageInterface):
    """Postgres-backed storage with hot relational projections."""

    def __init__(self, dsn: str) -> None:
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "PostgresStorage requires the postgres extra: "
                "pip install 'selfevals[postgres]'"
            ) from exc
        self._dsn = dsn
        self._conn: Any = psycopg.connect(dsn, autocommit=True)
        self._apply_schema()

    @property
    def connection(self) -> Any:
        return self._conn

    def open(self, workspace_id: str) -> WorkspaceScope:
        if not workspace_id:
            raise ValueError("workspace_id must be a non-empty string")
        return _PostgresScope(self._conn, workspace_id)

    def close(self) -> None:
        self._conn.close()

    def _apply_schema(self) -> None:
        with self._conn.cursor() as cur:
            cur.execute(_SCHEMA_SQL)

    # Hot query helpers used by the API. SQLite falls back to generic paths.
    def list_workspace_summaries(self) -> list[WorkspaceSummary]:
        from selfevals.api.schemas import WorkspaceSummary
        from selfevals.schemas.workspace import Workspace

        rows = self._fetchall(
            """
            SELECT w.payload, COUNT(e.id)::int AS experiment_count, MAX(i.updated_at) AS last_run_at
            FROM workspaces w
            LEFT JOIN experiments e ON e.workspace_id = w.id
            LEFT JOIN iterations i ON i.workspace_id = w.id
            GROUP BY w.id, w.payload
            ORDER BY w.created_at DESC
            """
        )
        out: list[WorkspaceSummary] = []
        for payload, exp_count, last_run_at in rows:
            ws = Workspace.model_validate(_json(payload))
            out.append(
                WorkspaceSummary(
                    id=ws.id,
                    slug=ws.slug,
                    name=ws.name,
                    description=ws.description,
                    owner_id=ws.owner_id,
                    created_at=ws.created_at,
                    experiment_count=int(exp_count or 0),
                    last_run_at=last_run_at,
                )
            )
        return out

    def workspace_by_slug_owner(self, *, slug: str, user_id: str) -> Any | None:
        from selfevals.schemas.workspace import Workspace

        row = self._fetchone_optional(
            """
            SELECT payload FROM workspaces
            WHERE slug = %s AND owner_id = %s
            LIMIT 1
            """,
            [slug, user_id],
        )
        if row is None:
            return None
        return Workspace.model_validate(_json(row[0]))

    def list_experiments_page(
        self,
        *,
        workspace_id: str,
        limit: int,
        offset: int,
        state: str | None,
        feature: str | None,
    ) -> tuple[list[Experiment], int, dict[str, int]]:
        from selfevals.schemas.experiment import Experiment

        clauses = ["workspace_id = %s"]
        params: list[Any] = [workspace_id]
        if state is not None:
            clauses.append("state = %s")
            params.append(state)
        if feature is not None:
            clauses.append("target_features ? %s")
            params.append(feature)
        where = " AND ".join(clauses)
        total = int(self._fetchone(f"SELECT COUNT(1) FROM experiments WHERE {where}", params)[0])
        rows = self._fetchall(
            f"""
            SELECT payload FROM experiments
            WHERE {where}
            ORDER BY updated_at DESC
            LIMIT %s OFFSET %s
            """,
            [*params, limit, offset],
        )
        experiments = [Experiment.model_validate(_json(row[0])) for row in rows]
        ids = [e.id for e in experiments]
        counts: dict[str, int] = {}
        if ids:
            for exp_id, count in self._fetchall(
                """
                SELECT experiment_id, COUNT(1)::int FROM iterations
                WHERE workspace_id = %s AND experiment_id = ANY(%s)
                GROUP BY experiment_id
                """,
                [workspace_id, ids],
            ):
                counts[str(exp_id)] = int(count)
        return experiments, total, counts

    def eval_cases_for_experiment(self, workspace_id: str, experiment_id: str) -> list[EvalCase]:
        from selfevals.schemas.eval_case import EvalCase

        rows = self._fetchall(
            """
            SELECT payload FROM eval_cases
            WHERE workspace_id = %s AND experiment_id = %s
            ORDER BY name ASC
            """,
            [workspace_id, experiment_id],
        )
        return [EvalCase.model_validate(_json(row[0])) for row in rows]

    def latest_trace_refs_by_case(
        self, workspace_id: str, experiment_id: str
    ) -> dict[str, tuple[str, str]]:
        rows = self._fetchall(
            """
            SELECT DISTINCT ON (eval_case_id) eval_case_id, run_id, id
            FROM traces
            WHERE workspace_id = %s AND experiment_id = %s AND eval_case_id IS NOT NULL
            ORDER BY eval_case_id, iteration DESC NULLS LAST, started_at DESC
            """,
            [workspace_id, experiment_id],
        )
        return {str(case_id): (str(run_id), str(trace_id)) for case_id, run_id, trace_id in rows}

    def traces_for_experiment_iteration(
        self, workspace_id: str, experiment_id: str, iteration: int
    ) -> list[Trace]:
        from selfevals.schemas.trace import Trace

        rows = self._fetchall(
            """
            SELECT payload FROM traces
            WHERE workspace_id = %s AND experiment_id = %s AND iteration = %s
            ORDER BY started_at ASC
            """,
            [workspace_id, experiment_id, iteration],
        )
        return [Trace.model_validate(_json(row[0])) for row in rows]

    def trace_by_id_or_run_id(self, workspace_id: str, trace_id: str) -> Trace | None:
        from selfevals.schemas.trace import Trace

        row = self._fetchone_optional(
            """
            SELECT payload FROM traces
            WHERE workspace_id = %s AND (id = %s OR run_id = %s)
            LIMIT 1
            """,
            [workspace_id, trace_id, trace_id],
        )
        if row is None:
            return None
        return Trace.model_validate(_json(row[0]))

    def traces_by_thread_id(self, workspace_id: str, thread_id: str) -> list[Trace]:
        from selfevals.schemas.trace import Trace

        rows = self._fetchall(
            """
            SELECT payload FROM traces
            WHERE workspace_id = %s AND thread_id = %s
            ORDER BY
              CASE WHEN thread_position IS NULL THEN 1 ELSE 0 END,
              thread_position ASC,
              started_at ASC
            """,
            [workspace_id, thread_id],
        )
        return [Trace.model_validate(_json(row[0])) for row in rows]

    def pass_rate_metrics(
        self,
        *,
        workspace_id: str,
        start: Any | None = None,
        end: Any | None = None,
        experiment_id: str | None = None,
        grader: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses, params = _metrics_trace_clauses(
            workspace_id=workspace_id,
            start=start,
            end=end,
            experiment_id=experiment_id,
        )
        if grader is not None:
            clauses.append("gr.grader = %s")
            params.append(grader)
        rows = self._fetchall(
            f"""
            SELECT gr.grader, gr.label, COUNT(1)::int AS count
            FROM trace_grader_results gr
            JOIN traces t ON t.id = gr.trace_id
            WHERE {" AND ".join(clauses)}
            GROUP BY gr.grader, gr.label
            ORDER BY count DESC, gr.grader ASC, gr.label ASC
            """,
            params,
        )
        return [{"grader": str(gr), "label": str(label), "count": int(count)} for gr, label, count in rows]

    def failure_mode_metrics(
        self,
        *,
        workspace_id: str,
        start: Any | None = None,
        end: Any | None = None,
        experiment_id: str | None = None,
        grader: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses, params = _metrics_trace_clauses(
            workspace_id=workspace_id,
            start=start,
            end=end,
            experiment_id=experiment_id,
        )
        if grader is not None:
            clauses.append("gr.grader = %s")
            params.append(grader)
        rows = self._fetchall(
            f"""
            SELECT fm.failure_mode, COUNT(1)::int AS count
            FROM trace_grader_results gr
            JOIN traces t ON t.id = gr.trace_id
            CROSS JOIN LATERAL jsonb_array_elements_text(gr.failure_modes) AS fm(failure_mode)
            WHERE {" AND ".join(clauses)}
            GROUP BY fm.failure_mode
            ORDER BY count DESC, fm.failure_mode ASC
            """,
            params,
        )
        return [{"failure_mode": str(mode), "count": int(count)} for mode, count in rows]

    def tool_metrics(
        self,
        *,
        workspace_id: str,
        start: Any | None = None,
        end: Any | None = None,
        experiment_id: str | None = None,
        tool_name: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses, params = _metrics_trace_clauses(
            workspace_id=workspace_id,
            start=start,
            end=end,
            experiment_id=experiment_id,
        )
        if tool_name is not None:
            clauses.append("tc.tool_name = %s")
            params.append(tool_name)
        rows = self._fetchall(
            f"""
            SELECT
              tc.tool_name,
              tc.status,
              COUNT(1)::int AS count,
              SUM(CASE WHEN tc.status <> 'ok' OR tc.error IS NOT NULL THEN 1 ELSE 0 END)::int
                AS error_count,
              AVG(tc.duration_ms)::double precision AS avg_duration_ms,
              SUM(tc.retry_count)::int AS retry_count
            FROM tool_calls tc
            JOIN traces t ON t.id = tc.trace_id
            WHERE {" AND ".join(clauses)}
            GROUP BY tc.tool_name, tc.status
            ORDER BY count DESC, tc.tool_name ASC, tc.status ASC
            """,
            params,
        )
        return [
            {
                "tool_name": str(tool),
                "status": str(status),
                "count": int(count),
                "error_count": int(errors or 0),
                "avg_duration_ms": float(avg) if avg is not None else None,
                "retry_count": int(retries or 0),
            }
            for tool, status, count, errors, avg, retries in rows
        ]

    def cost_metrics(
        self,
        *,
        workspace_id: str,
        start: Any | None = None,
        end: Any | None = None,
        experiment_id: str | None = None,
        model: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses, params = _metrics_trace_clauses(
            workspace_id=workspace_id,
            start=start,
            end=end,
            experiment_id=experiment_id,
        )
        if model is not None:
            clauses.append("lc.model = %s")
            params.append(model)
        rows = self._fetchall(
            f"""
            SELECT
              lc.provider,
              lc.model,
              COUNT(1)::int AS call_count,
              SUM(COALESCE(lc.cost_usd, 0.0))::double precision AS total_cost_usd,
              AVG(COALESCE(lc.cost_usd, 0.0))::double precision AS avg_cost_usd
            FROM llm_calls lc
            JOIN traces t ON t.id = lc.trace_id
            WHERE {" AND ".join(clauses)}
            GROUP BY lc.provider, lc.model
            ORDER BY total_cost_usd DESC, lc.provider ASC, lc.model ASC
            """,
            params,
        )
        return [
            {
                "provider": str(provider),
                "model": str(model_name),
                "call_count": int(count),
                "total_cost_usd": float(total or 0.0),
                "avg_cost_usd": float(avg or 0.0),
            }
            for provider, model_name, count, total, avg in rows
        ]

    def token_metrics(
        self,
        *,
        workspace_id: str,
        start: Any | None = None,
        end: Any | None = None,
        experiment_id: str | None = None,
        model: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses, params = _metrics_trace_clauses(
            workspace_id=workspace_id,
            start=start,
            end=end,
            experiment_id=experiment_id,
        )
        if model is not None:
            clauses.append("lc.model = %s")
            params.append(model)
        rows = self._fetchall(
            f"""
            SELECT
              lc.provider,
              lc.model,
              COUNT(1)::int AS call_count,
              SUM(COALESCE(lc.input_tokens, 0))::bigint AS input_tokens,
              SUM(COALESCE(lc.output_tokens, 0))::bigint AS output_tokens,
              SUM(COALESCE(lc.reasoning_tokens, 0))::bigint AS reasoning_tokens,
              SUM(COALESCE(lc.total_tokens, 0))::bigint AS total_tokens
            FROM llm_calls lc
            JOIN traces t ON t.id = lc.trace_id
            WHERE {" AND ".join(clauses)}
            GROUP BY lc.provider, lc.model
            ORDER BY total_tokens DESC, lc.provider ASC, lc.model ASC
            """,
            params,
        )
        return [
            {
                "provider": str(provider),
                "model": str(model_name),
                "call_count": int(count),
                "input_tokens": int(input_tokens or 0),
                "output_tokens": int(output_tokens or 0),
                "reasoning_tokens": int(reasoning_tokens or 0),
                "total_tokens": int(total_tokens or 0),
            }
            for provider, model_name, count, input_tokens, output_tokens, reasoning_tokens, total_tokens in rows
        ]

    def latency_metrics(
        self,
        *,
        workspace_id: str,
        start: Any | None = None,
        end: Any | None = None,
        experiment_id: str | None = None,
    ) -> list[dict[str, Any]]:
        metrics: list[dict[str, Any]] = []
        trace_clauses, trace_params = _metrics_trace_clauses(
            workspace_id=workspace_id,
            start=start,
            end=end,
            experiment_id=experiment_id,
        )
        row = self._fetchone(
            f"""
            SELECT
              COUNT(duration_ms)::int,
              percentile_cont(0.50) WITHIN GROUP (ORDER BY duration_ms)::double precision,
              percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms)::double precision,
              percentile_cont(0.99) WITHIN GROUP (ORDER BY duration_ms)::double precision
            FROM traces t
            WHERE {" AND ".join(trace_clauses)} AND duration_ms IS NOT NULL
            """,
            trace_params,
        )
        _append_latency_metric(metrics, "trace_duration_ms", row)

        tool_clauses, tool_params = _metrics_trace_clauses(
            workspace_id=workspace_id,
            start=start,
            end=end,
            experiment_id=experiment_id,
        )
        row = self._fetchone(
            f"""
            SELECT
              COUNT(tc.duration_ms)::int,
              percentile_cont(0.50) WITHIN GROUP (ORDER BY tc.duration_ms)::double precision,
              percentile_cont(0.95) WITHIN GROUP (ORDER BY tc.duration_ms)::double precision,
              percentile_cont(0.99) WITHIN GROUP (ORDER BY tc.duration_ms)::double precision
            FROM tool_calls tc
            JOIN traces t ON t.id = tc.trace_id
            WHERE {" AND ".join(tool_clauses)}
            """,
            tool_params,
        )
        _append_latency_metric(metrics, "tool_duration_ms", row)

        ttft_clauses, ttft_params = _metrics_trace_clauses(
            workspace_id=workspace_id,
            start=start,
            end=end,
            experiment_id=experiment_id,
        )
        row = self._fetchone(
            f"""
            SELECT
              COUNT(lc.time_to_first_token_ms)::int,
              percentile_cont(0.50) WITHIN GROUP (ORDER BY lc.time_to_first_token_ms)::double precision,
              percentile_cont(0.95) WITHIN GROUP (ORDER BY lc.time_to_first_token_ms)::double precision,
              percentile_cont(0.99) WITHIN GROUP (ORDER BY lc.time_to_first_token_ms)::double precision
            FROM llm_calls lc
            JOIN traces t ON t.id = lc.trace_id
            WHERE {" AND ".join(ttft_clauses)} AND lc.time_to_first_token_ms IS NOT NULL
            """,
            ttft_params,
        )
        _append_latency_metric(metrics, "ttft_ms", row)
        return metrics

    def _fetchone(self, sql: str, params: Sequence[Any] | None = None) -> tuple[Any, ...]:
        row = self._fetchone_optional(sql, params)
        if row is None:
            raise RuntimeError("expected row")
        return row

    def _fetchone_optional(
        self, sql: str, params: Sequence[Any] | None = None
    ) -> tuple[Any, ...] | None:
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            if row is None:
                return None
            return tuple(row)

    def _fetchall(self, sql: str, params: Sequence[Any] | None = None) -> list[tuple[Any, ...]]:
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            return [tuple(row) for row in cur.fetchall()]


class _PostgresScope(WorkspaceScope):
    def __init__(self, conn: Any, workspace_id: str) -> None:
        self._conn = conn
        self.workspace_id = workspace_id
        self._closed = False

    def close(self) -> None:
        self._closed = True

    def __enter__(self) -> Self:
        if self._closed:
            raise RuntimeError("scope has been closed")
        return self

    def put_entity(self, entity: BaseEntity) -> None:
        self._guard_open()
        self.assert_owns(entity)
        entity_type = _entity_type_name(type(entity))
        payload = entity.model_dump(mode="json")
        payload_json = _jsonb(payload)
        now_iso = utc_now().isoformat()
        with self._conn.cursor() as cur:
            existing = cur.execute(
                "SELECT version FROM entities WHERE entity_type = %s AND id = %s",
                (entity_type, entity.id),
            ).fetchone()
            if existing is not None:
                stored_version = int(existing[0])
                if stored_version != entity.version - 1 and stored_version != entity.version:
                    raise OptimisticConcurrencyError(
                        entity_type=entity_type,
                        entity_id=entity.id,
                        expected=entity.version,
                        found=stored_version,
                    )
            cur.execute(
                """
                INSERT INTO entities
                  (entity_type, id, workspace_id, version, created_at, updated_at, deleted_at, payload)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (entity_type, id) DO UPDATE SET
                  workspace_id = EXCLUDED.workspace_id,
                  version = EXCLUDED.version,
                  updated_at = %s,
                  deleted_at = EXCLUDED.deleted_at,
                  payload = EXCLUDED.payload
                """,
                (
                    entity_type,
                    entity.id,
                    entity.workspace_id,
                    entity.version,
                    entity.created_at.isoformat(),
                    entity.updated_at.isoformat(),
                    entity.deleted_at.isoformat() if entity.deleted_at else None,
                    payload_json,
                    now_iso,
                ),
            )
            _upsert_hot_projection(cur, entity_type, payload, payload_json)

    def get_entity(self, entity_type: type[BaseEntity], entity_id: str) -> BaseEntity:
        self._guard_open()
        type_tag = _entity_type_name(entity_type)
        with self._conn.cursor() as cur:
            row = cur.execute(
                """
                SELECT workspace_id, payload FROM entities
                WHERE entity_type = %s AND id = %s AND workspace_id = %s
                """,
                (type_tag, entity_id, self.workspace_id),
            ).fetchone()
            if row is None:
                cross = cur.execute(
                    "SELECT workspace_id FROM entities WHERE entity_type = %s AND id = %s",
                    (type_tag, entity_id),
                ).fetchone()
                if cross is not None:
                    raise WorkspaceMismatchError(self.workspace_id, cross[0])
                raise EntityNotFoundError(type_tag, entity_id, self.workspace_id)
        return entity_type.model_validate(_json(row[1]))

    def list_entities(
        self,
        entity_type: type[BaseEntity],
        filter_: ListFilter | None = None,
    ) -> list[BaseEntity]:
        self._guard_open()
        type_tag = _entity_type_name(entity_type)
        filter_ = filter_ or ListFilter()
        if filter_.order_by not in _ORDERABLE_COLUMNS:
            raise ValueError(f"unsupported order_by column: {filter_.order_by!r}")
        clauses = ["workspace_id = %s", "entity_type = %s"]
        params: list[Any] = [self.workspace_id, type_tag]
        for key, value in filter_.where.items():
            if key in _ORDERABLE_COLUMNS:
                clauses.append(f"{key} = %s")
                params.append(value)
            else:
                clauses.append("payload #>> %s = %s")
                params.extend([key.split("."), str(value)])
        sql = (
            "SELECT payload FROM entities "
            f"WHERE {' AND '.join(clauses)} "
            f"ORDER BY {filter_.order_by} {'DESC' if filter_.order_desc else 'ASC'}"
        )
        if filter_.limit is not None:
            sql += " LIMIT %s OFFSET %s"
            params.extend([filter_.limit, filter_.offset])
        with self._conn.cursor() as cur:
            rows = cur.execute(sql, params).fetchall()
        return [entity_type.model_validate(_json(row[0])) for row in rows]

    def delete_entity(self, entity_type: type[BaseEntity], entity_id: str) -> None:
        self._guard_open()
        type_tag = _entity_type_name(entity_type)
        with self._conn.cursor() as cur:
            owner = cur.execute(
                "SELECT workspace_id FROM entities WHERE entity_type = %s AND id = %s",
                (type_tag, entity_id),
            ).fetchone()
            if owner is None:
                raise EntityNotFoundError(type_tag, entity_id, self.workspace_id)
            if owner[0] != self.workspace_id:
                raise WorkspaceMismatchError(self.workspace_id, owner[0])
            cur.execute(
                "DELETE FROM entities WHERE entity_type = %s AND id = %s",
                (type_tag, entity_id),
            )
            _delete_hot_projection(cur, type_tag, entity_id)

    def exists(self, entity_type: type[BaseEntity], entity_id: str) -> bool:
        self._guard_open()
        type_tag = _entity_type_name(entity_type)
        with self._conn.cursor() as cur:
            row = cur.execute(
                """
                SELECT 1 FROM entities
                WHERE entity_type = %s AND id = %s AND workspace_id = %s
                """,
                (type_tag, entity_id, self.workspace_id),
            ).fetchone()
        return row is not None

    def _guard_open(self) -> None:
        if self._closed:
            raise RuntimeError("scope has been closed; open a new one")


def _json(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _jsonb(value: Any) -> Any:
    from psycopg.types.json import Jsonb

    return Jsonb(value)


def _upsert_hot_projection(cur: Any, entity_type: str, payload: dict[str, Any], payload_json: Any) -> None:
    if entity_type == "Workspace":
        cur.execute(
            """
            INSERT INTO workspaces (id, workspace_id, slug, name, owner_id, created_at, updated_at, payload)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
              slug = EXCLUDED.slug,
              name = EXCLUDED.name,
              owner_id = EXCLUDED.owner_id,
              updated_at = EXCLUDED.updated_at,
              payload = EXCLUDED.payload
            """,
            (
                payload["id"],
                payload["workspace_id"],
                payload.get("slug"),
                payload.get("name"),
                payload.get("owner_id"),
                payload.get("created_at"),
                payload.get("updated_at"),
                payload_json,
            ),
        )
    elif entity_type == "Experiment":
        taxonomy = payload.get("taxonomy") or {}
        target_features = taxonomy.get("target_features") or []
        cur.execute(
            """
            INSERT INTO experiments
              (id, workspace_id, name, state, target_features, created_at, updated_at, payload)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
              workspace_id = EXCLUDED.workspace_id,
              name = EXCLUDED.name,
              state = EXCLUDED.state,
              target_features = EXCLUDED.target_features,
              updated_at = EXCLUDED.updated_at,
              payload = EXCLUDED.payload
            """,
            (
                payload["id"],
                payload["workspace_id"],
                payload.get("name"),
                payload.get("state"),
                _jsonb({f: True for f in target_features}),
                payload.get("created_at"),
                payload.get("updated_at"),
                payload_json,
            ),
        )
    elif entity_type == "IterationRecord":
        metrics = payload.get("metrics") or {}
        primary = metrics.get("primary") or {}
        decision = payload.get("decision") or {}
        cur.execute(
            """
            INSERT INTO iterations
              (id, workspace_id, experiment_id, iteration, state, primary_metric_name,
               primary_metric_value, decision_outcome, cost_usd, duration_seconds,
               created_at, updated_at, payload)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
              state = EXCLUDED.state,
              primary_metric_name = EXCLUDED.primary_metric_name,
              primary_metric_value = EXCLUDED.primary_metric_value,
              decision_outcome = EXCLUDED.decision_outcome,
              cost_usd = EXCLUDED.cost_usd,
              duration_seconds = EXCLUDED.duration_seconds,
              updated_at = EXCLUDED.updated_at,
              payload = EXCLUDED.payload
            """,
            (
                payload["id"],
                payload["workspace_id"],
                payload.get("experiment_id"),
                payload.get("iteration"),
                payload.get("state"),
                primary.get("name"),
                primary.get("value"),
                decision.get("outcome"),
                payload.get("cost_usd"),
                payload.get("duration_seconds"),
                payload.get("created_at"),
                payload.get("updated_at"),
                payload_json,
            ),
        )
    elif entity_type == "EvalCase":
        taxonomy = payload.get("taxonomy") or {}
        feature = taxonomy.get("feature") or {}
        cur.execute(
            """
            INSERT INTO eval_cases
              (id, workspace_id, experiment_id, name, task_type, feature_primary,
               holdout, created_at, updated_at, payload)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
              experiment_id = EXCLUDED.experiment_id,
              name = EXCLUDED.name,
              task_type = EXCLUDED.task_type,
              feature_primary = EXCLUDED.feature_primary,
              holdout = EXCLUDED.holdout,
              updated_at = EXCLUDED.updated_at,
              payload = EXCLUDED.payload
            """,
            (
                payload["id"],
                payload["workspace_id"],
                payload.get("experiment_id"),
                payload.get("name"),
                payload.get("task_type"),
                feature.get("primary"),
                bool(payload.get("holdout", False)),
                payload.get("created_at"),
                payload.get("updated_at"),
                payload_json,
            ),
        )
    elif entity_type == "Trace":
        run = payload.get("run") or {}
        env = payload.get("environment") or {}
        metrics = payload.get("metrics") or {}
        trace_id = payload["id"]
        workspace_id = payload["workspace_id"]
        cur.execute(
            """
            INSERT INTO traces
              (id, workspace_id, run_id, experiment_id, iteration, eval_case_id,
               thread_id, thread_position, started_at, ended_at, final_state,
               tool_call_count, cost_usd, duration_ms, created_at, updated_at, payload)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
              run_id = EXCLUDED.run_id,
              experiment_id = EXCLUDED.experiment_id,
              iteration = EXCLUDED.iteration,
              eval_case_id = EXCLUDED.eval_case_id,
              thread_id = EXCLUDED.thread_id,
              thread_position = EXCLUDED.thread_position,
              ended_at = EXCLUDED.ended_at,
              final_state = EXCLUDED.final_state,
              tool_call_count = EXCLUDED.tool_call_count,
              cost_usd = EXCLUDED.cost_usd,
              duration_ms = EXCLUDED.duration_ms,
              updated_at = EXCLUDED.updated_at,
              payload = EXCLUDED.payload
            """,
            (
                trace_id,
                workspace_id,
                run.get("run_id"),
                run.get("experiment_id"),
                run.get("iteration"),
                run.get("eval_case_id"),
                run.get("thread_id"),
                run.get("thread_position"),
                env.get("started_at"),
                env.get("ended_at"),
                (payload.get("final_state") or {}).get("status"),
                metrics.get("tool_call_count"),
                metrics.get("total_cost_usd"),
                metrics.get("total_duration_ms"),
                payload.get("created_at"),
                payload.get("updated_at"),
                payload_json,
            ),
        )
        _replace_trace_facts(cur, payload)
    elif entity_type == "DecisionRecord":
        cur.execute(
            """
            INSERT INTO decisions
              (id, workspace_id, experiment_id, iteration, outcome, created_at, updated_at, payload)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
              outcome = EXCLUDED.outcome,
              updated_at = EXCLUDED.updated_at,
              payload = EXCLUDED.payload
            """,
            (
                payload["id"],
                payload["workspace_id"],
                payload.get("experiment_id"),
                payload.get("iteration"),
                payload.get("outcome"),
                payload.get("created_at"),
                payload.get("updated_at"),
                payload_json,
            ),
        )
    elif entity_type == "RunJob":
        cur.execute(
            """
            INSERT INTO run_jobs
              (id, workspace_id, experiment_id, status, attempt, max_attempts,
               lease_owner, lease_expires_at, cancel_requested_at, started_at,
               finished_at, last_error, created_at, updated_at, payload)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
              status = EXCLUDED.status,
              attempt = EXCLUDED.attempt,
              max_attempts = EXCLUDED.max_attempts,
              lease_owner = EXCLUDED.lease_owner,
              lease_expires_at = EXCLUDED.lease_expires_at,
              cancel_requested_at = EXCLUDED.cancel_requested_at,
              started_at = EXCLUDED.started_at,
              finished_at = EXCLUDED.finished_at,
              last_error = EXCLUDED.last_error,
              updated_at = EXCLUDED.updated_at,
              payload = EXCLUDED.payload
            """,
            (
                payload["id"],
                payload["workspace_id"],
                payload.get("experiment_id"),
                payload.get("status"),
                payload.get("attempt"),
                payload.get("max_attempts"),
                payload.get("lease_owner"),
                payload.get("lease_expires_at"),
                payload.get("cancel_requested_at"),
                payload.get("started_at"),
                payload.get("finished_at"),
                payload.get("last_error"),
                payload.get("created_at"),
                payload.get("updated_at"),
                payload_json,
            ),
        )


def _delete_hot_projection(cur: Any, entity_type: str, entity_id: str) -> None:
    table = {
        "Workspace": "workspaces",
        "Experiment": "experiments",
        "IterationRecord": "iterations",
        "EvalCase": "eval_cases",
        "Trace": "traces",
        "DecisionRecord": "decisions",
        "RunJob": "run_jobs",
    }.get(entity_type)
    if table is not None:
        cur.execute(f"DELETE FROM {table} WHERE id = %s", (entity_id,))
    if entity_type == "Trace":
        _delete_trace_facts(cur, entity_id)


def _replace_trace_facts(cur: Any, payload: dict[str, Any]) -> None:
    trace_id = str(payload["id"])
    workspace_id = str(payload["workspace_id"])
    run = payload.get("run") or {}
    experiment_id = run.get("experiment_id")
    iteration = run.get("iteration")
    _delete_trace_facts(cur, trace_id)
    for index, span in enumerate(payload.get("spans") or []):
        if not isinstance(span, dict):
            continue
        span_json = _jsonb(span)
        span_id = str(span.get("id") or f"{trace_id}:{index}")
        kind = span.get("kind")
        cur.execute(
            """
            INSERT INTO trace_spans
              (id, trace_id, workspace_id, run_id, experiment_id, iteration,
               span_index, parent_id, kind, name, started_at, duration_ms, payload)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                span_id,
                trace_id,
                workspace_id,
                run.get("run_id"),
                experiment_id,
                iteration,
                index,
                span.get("parent_id"),
                kind,
                span.get("name"),
                span.get("started_at"),
                span.get("duration_ms"),
                span_json,
            ),
        )
        if kind == "llm_call":
            tokens = span.get("tokens") or {}
            cost = span.get("cost_usd") or {}
            output = span.get("output") or {}
            cur.execute(
                """
                INSERT INTO llm_calls
                  (span_id, trace_id, workspace_id, run_id, experiment_id, iteration,
                   provider, model, model_version_pinned, input_tokens, output_tokens,
                   reasoning_tokens, total_tokens, cost_usd, stop_reason,
                   time_to_first_token_ms, tokens_per_second, retries, cache_hit,
                   tools_requested_count, payload)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    span_id,
                    trace_id,
                    workspace_id,
                    run.get("run_id"),
                    experiment_id,
                    iteration,
                    span.get("provider"),
                    span.get("model"),
                    span.get("model_version_pinned"),
                    tokens.get("input"),
                    tokens.get("output"),
                    tokens.get("reasoning"),
                    tokens.get("total"),
                    cost.get("total"),
                    output.get("stop_reason"),
                    span.get("time_to_first_token_ms"),
                    span.get("tokens_per_second"),
                    span.get("retries"),
                    span.get("cache_hit"),
                    len(output.get("tool_use_requested") or []),
                    span_json,
                ),
            )
        elif kind == "tool_call":
            cur.execute(
                """
                INSERT INTO tool_calls
                  (span_id, trace_id, workspace_id, run_id, experiment_id, iteration,
                   tool_name, tool_version, tool_use_id, status, error,
                   duration_ms, retry_count, sandboxed, payload)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    span_id,
                    trace_id,
                    workspace_id,
                    run.get("run_id"),
                    experiment_id,
                    iteration,
                    span.get("tool_name"),
                    span.get("tool_version"),
                    span.get("tool_use_id"),
                    span.get("status"),
                    span.get("error"),
                    span.get("duration_ms"),
                    len(span.get("retry_chain") or []),
                    span.get("sandboxed"),
                    span_json,
                ),
            )
    for index, grader in enumerate(payload.get("grader_results") or []):
        if not isinstance(grader, dict):
            continue
        cur.execute(
            """
            INSERT INTO trace_grader_results
              (trace_id, workspace_id, run_id, experiment_id, iteration,
               result_index, grader, label, score, confidence, failure_modes, payload)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                trace_id,
                workspace_id,
                run.get("run_id"),
                experiment_id,
                iteration,
                index,
                grader.get("grader"),
                grader.get("label"),
                grader.get("score"),
                grader.get("confidence"),
                _jsonb(grader.get("failure_modes") or []),
                _jsonb(grader),
            ),
        )


def _delete_trace_facts(cur: Any, trace_id: str) -> None:
    cur.execute("DELETE FROM trace_grader_results WHERE trace_id = %s", (trace_id,))
    cur.execute("DELETE FROM tool_calls WHERE trace_id = %s", (trace_id,))
    cur.execute("DELETE FROM llm_calls WHERE trace_id = %s", (trace_id,))
    cur.execute("DELETE FROM trace_spans WHERE trace_id = %s", (trace_id,))


def _metrics_trace_clauses(
    *,
    workspace_id: str,
    start: Any | None,
    end: Any | None,
    experiment_id: str | None,
) -> tuple[list[str], list[Any]]:
    clauses = ["t.workspace_id = %s"]
    params: list[Any] = [workspace_id]
    if experiment_id is not None:
        clauses.append("t.experiment_id = %s")
        params.append(experiment_id)
    if start is not None:
        clauses.append("t.started_at >= %s")
        params.append(start)
    if end is not None:
        clauses.append("t.started_at <= %s")
        params.append(end)
    return clauses, params


def _append_latency_metric(
    metrics: list[dict[str, Any]],
    metric: str,
    row: tuple[Any, ...],
) -> None:
    count, p50, p95, p99 = row
    if int(count or 0) <= 0:
        return
    metrics.append(
        {
            "metric": metric,
            "count": int(count),
            "p50_ms": float(p50) if p50 is not None else None,
            "p95_ms": float(p95) if p95 is not None else None,
            "p99_ms": float(p99) if p99 is not None else None,
        }
    )


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS entities (
    entity_type  TEXT NOT NULL,
    id           TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    version      INTEGER NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL,
    deleted_at   TIMESTAMPTZ,
    payload      JSONB NOT NULL,
    PRIMARY KEY (entity_type, id)
);
CREATE INDEX IF NOT EXISTS idx_entities_workspace_type
    ON entities (workspace_id, entity_type);
CREATE INDEX IF NOT EXISTS idx_entities_workspace_type_created
    ON entities (workspace_id, entity_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_entities_workspace_type_updated
    ON entities (workspace_id, entity_type, updated_at DESC);

CREATE TABLE IF NOT EXISTS objects (
    pointer      TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    key          TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    byte_size    INTEGER NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_objects_workspace ON objects (workspace_id);
CREATE INDEX IF NOT EXISTS idx_objects_content_hash ON objects (content_hash);

CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    name TEXT NOT NULL,
    owner_id TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS experiments (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    state TEXT NOT NULL,
    target_features JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_experiments_workspace_updated
    ON experiments (workspace_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_experiments_workspace_state
    ON experiments (workspace_id, state);
CREATE INDEX IF NOT EXISTS idx_experiments_target_features
    ON experiments USING GIN (target_features);

CREATE TABLE IF NOT EXISTS iterations (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    experiment_id TEXT NOT NULL,
    iteration INTEGER NOT NULL,
    state TEXT NOT NULL,
    primary_metric_name TEXT,
    primary_metric_value DOUBLE PRECISION,
    decision_outcome TEXT,
    cost_usd DOUBLE PRECISION,
    duration_seconds DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_iterations_workspace_experiment_iteration
    ON iterations (workspace_id, experiment_id, iteration);
CREATE INDEX IF NOT EXISTS idx_iterations_workspace_updated
    ON iterations (workspace_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS eval_cases (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    experiment_id TEXT,
    name TEXT NOT NULL,
    task_type TEXT NOT NULL,
    feature_primary TEXT,
    holdout BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_eval_cases_workspace_experiment
    ON eval_cases (workspace_id, experiment_id);

CREATE TABLE IF NOT EXISTS traces (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    experiment_id TEXT,
    iteration INTEGER,
    eval_case_id TEXT,
    thread_id TEXT,
    thread_position INTEGER,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    final_state TEXT NOT NULL,
    tool_call_count INTEGER,
    cost_usd DOUBLE PRECISION,
    duration_ms INTEGER,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_traces_workspace_run
    ON traces (workspace_id, run_id);
CREATE INDEX IF NOT EXISTS idx_traces_workspace_thread
    ON traces (workspace_id, thread_id, thread_position, started_at);
CREATE INDEX IF NOT EXISTS idx_traces_workspace_experiment_iteration
    ON traces (workspace_id, experiment_id, iteration);
CREATE INDEX IF NOT EXISTS idx_traces_workspace_case_started
    ON traces (workspace_id, eval_case_id, started_at DESC);

CREATE TABLE IF NOT EXISTS trace_spans (
    id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    experiment_id TEXT,
    iteration INTEGER,
    span_index INTEGER NOT NULL,
    parent_id TEXT,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    duration_ms INTEGER NOT NULL,
    payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trace_spans_workspace_trace_index
    ON trace_spans (workspace_id, trace_id, span_index);
CREATE INDEX IF NOT EXISTS idx_trace_spans_workspace_kind_started
    ON trace_spans (workspace_id, kind, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_trace_spans_workspace_experiment_iteration
    ON trace_spans (workspace_id, experiment_id, iteration);

CREATE TABLE IF NOT EXISTS llm_calls (
    span_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    experiment_id TEXT,
    iteration INTEGER,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    model_version_pinned TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    reasoning_tokens INTEGER,
    total_tokens INTEGER,
    cost_usd DOUBLE PRECISION,
    stop_reason TEXT,
    time_to_first_token_ms INTEGER,
    tokens_per_second DOUBLE PRECISION,
    retries INTEGER,
    cache_hit BOOLEAN,
    tools_requested_count INTEGER,
    payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_workspace_model
    ON llm_calls (workspace_id, provider, model);
CREATE INDEX IF NOT EXISTS idx_llm_calls_workspace_experiment_iteration
    ON llm_calls (workspace_id, experiment_id, iteration);

CREATE TABLE IF NOT EXISTS tool_calls (
    span_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    experiment_id TEXT,
    iteration INTEGER,
    tool_name TEXT NOT NULL,
    tool_version TEXT,
    tool_use_id TEXT,
    status TEXT NOT NULL,
    error TEXT,
    duration_ms INTEGER NOT NULL,
    retry_count INTEGER NOT NULL,
    sandboxed BOOLEAN NOT NULL DEFAULT FALSE,
    payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tool_calls_workspace_tool
    ON tool_calls (workspace_id, tool_name, status);
CREATE INDEX IF NOT EXISTS idx_tool_calls_workspace_experiment_iteration
    ON tool_calls (workspace_id, experiment_id, iteration);

CREATE TABLE IF NOT EXISTS trace_grader_results (
    trace_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    experiment_id TEXT,
    iteration INTEGER,
    result_index INTEGER NOT NULL,
    grader TEXT NOT NULL,
    label TEXT NOT NULL,
    score DOUBLE PRECISION,
    confidence DOUBLE PRECISION,
    failure_modes JSONB NOT NULL DEFAULT '[]'::jsonb,
    payload JSONB NOT NULL,
    PRIMARY KEY (trace_id, result_index)
);
CREATE INDEX IF NOT EXISTS idx_trace_grader_results_workspace_label
    ON trace_grader_results (workspace_id, grader, label);
CREATE INDEX IF NOT EXISTS idx_trace_grader_results_workspace_experiment_iteration
    ON trace_grader_results (workspace_id, experiment_id, iteration);
CREATE INDEX IF NOT EXISTS idx_trace_grader_results_failure_modes
    ON trace_grader_results USING GIN (failure_modes);

CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    experiment_id TEXT NOT NULL,
    iteration INTEGER NOT NULL,
    outcome TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decisions_workspace_experiment_iteration
    ON decisions (workspace_id, experiment_id, iteration);

CREATE TABLE IF NOT EXISTS run_jobs (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    experiment_id TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    max_attempts INTEGER NOT NULL,
    lease_owner TEXT,
    lease_expires_at TIMESTAMPTZ,
    cancel_requested_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_run_jobs_workspace_status_updated
    ON run_jobs (workspace_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_run_jobs_lease
    ON run_jobs (lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_run_jobs_workspace_experiment
    ON run_jobs (workspace_id, experiment_id);
"""
