"""Mapper for Trace — the heaviest record, with polymorphic spans.

The main row flattens RunInfo/AgentSnapshotRef/EnvironmentInfo/FinalState/
TraceOutputs/TraceMetrics into prefixed columns. Spans are written to
``trace_spans`` (shared base + ``kind``) plus one detail table per kind; on read
each span is rebuilt into its concrete subtype. Grader results and links are
ordered child tables.
"""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from selfevals.schemas.trace import (
    AgentSnapshotRef,
    AgentTurnSpan,
    CostBreakdown,
    CustomSpan,
    DecisionSpan,
    EnvironmentInfo,
    ErrorSpan,
    FinalState,
    GraderResult,
    GuardrailCheckSpan,
    HandoffSpan,
    HumanInterventionSpan,
    LLMCallSpan,
    LLMOutput,
    MemoryReadSpan,
    MemoryWriteSpan,
    ReasoningBlock,
    RetrievalSpan,
    RetrievedDoc,
    RunInfo,
    Span,
    TokenBreakdown,
    ToolCallSpan,
    ToolUseRequest,
    Trace,
    TraceLink,
    TraceMetrics,
    TraceOutputs,
)
from selfevals.storage.postgres.mappers.base import (
    SHARED_COLUMNS,
    EntityMapper,
    register_mapper,
    shared_values,
)

_EXTRA_COLUMNS: tuple[str, ...] = (
    "schema_version",
    "snapshot_id",
    "run_id",
    "run_experiment_id",
    "run_iteration",
    "run_variant_id",
    "run_eval_case_id",
    "run_repetition",
    "run_seed",
    "run_thread_id",
    "run_thread_position",
    "agent_fleet_version",
    "agent_agent_id",
    "agent_agent_version",
    "agent_parameters_snapshot_id",
    "env_framework_version",
    "env_runtime",
    "env_sandbox",
    "env_tool_mocks",
    "env_started_at",
    "env_ended_at",
    "final_state_status",
    "final_state_error",
    "outputs_final_response_pointer",
    "outputs_structured_output",
    "metrics_total_tokens_in",
    "metrics_total_tokens_out",
    "metrics_total_cost_usd",
    "metrics_total_duration_ms",
    "metrics_tool_call_count",
    "metrics_llm_call_count",
    "metrics_retries",
    "metrics_recovery_events",
    "metrics_loop_detected",
)
_ALL_COLUMNS: tuple[str, ...] = (*SHARED_COLUMNS, *_EXTRA_COLUMNS)


class TraceMapper(EntityMapper[Trace]):
    entity_cls = Trace
    table = "traces"
    queryable_columns = frozenset(
        {*SHARED_COLUMNS, "run_id", "run_experiment_id", "run_iteration", "run_eval_case_id"}
    )
    # Accept the logical nested-path names callers used in the SQLite/JSON era.
    column_aliases = {  # noqa: RUF012 - intentional per-mapper mapping
        "run.experiment_id": "run_experiment_id",
        "run.iteration": "run_iteration",
        "run.run_id": "run_id",
        "run.eval_case_id": "run_eval_case_id",
    }

    def upsert(self, cur: Any, entity: Trace) -> None:
        t = entity
        run = t.run
        values = [
            *shared_values(t),
            t.schema_version,
            t.snapshot_id,
            run.run_id,
            run.experiment_id,
            run.iteration,
            run.variant_id,
            run.eval_case_id,
            run.repetition,
            run.seed,
            run.thread_id,
            run.thread_position,
            t.agent.fleet_version,
            t.agent.agent_id,
            t.agent.agent_version,
            t.agent.parameters_snapshot_id,
            t.environment.framework_version,
            t.environment.runtime,
            t.environment.sandbox.value,
            list(t.environment.tool_mocks),
            t.environment.started_at,
            t.environment.ended_at,
            t.final_state.status.value,
            t.final_state.error,
            t.outputs.final_response_pointer,
            Jsonb(t.outputs.structured_output) if t.outputs.structured_output is not None else None,
            t.metrics.total_tokens_in,
            t.metrics.total_tokens_out,
            t.metrics.total_cost_usd,
            t.metrics.total_duration_ms,
            t.metrics.tool_call_count,
            t.metrics.llm_call_count,
            t.metrics.retries,
            t.metrics.recovery_events,
            t.metrics.loop_detected,
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
        # Replace all facts (idempotent on update). Span child rows cascade from
        # trace_spans, and grader/link rows cascade from traces — but we delete
        # explicitly so an update with fewer spans/results doesn't leave stragglers.
        cur.execute("DELETE FROM trace_spans WHERE trace_id = %s", (t.id,))
        cur.execute("DELETE FROM trace_grader_results WHERE trace_id = %s", (t.id,))
        cur.execute("DELETE FROM trace_links WHERE trace_id = %s", (t.id,))
        for index, span in enumerate(t.spans):
            self._insert_span(cur, t.id, t.workspace_id, index, span)
        for index, gr in enumerate(t.grader_results):
            cur.execute(
                """
                INSERT INTO trace_grader_results
                  (trace_id, workspace_id, result_index, grader, label, score,
                   reason, reason_pointer, confidence, failure_modes, breakdown)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    t.id,
                    t.workspace_id,
                    index,
                    gr.grader,
                    gr.label,
                    gr.score,
                    gr.reason,
                    gr.reason_pointer,
                    gr.confidence,
                    list(gr.failure_modes),
                    Jsonb(gr.breakdown) if gr.breakdown is not None else None,
                ),
            )
        for index, link in enumerate(t.links):
            cur.execute(
                "INSERT INTO trace_links (trace_id, position, kind, target_trace_id) "
                "VALUES (%s, %s, %s, %s)",
                (t.id, index, link.kind, link.trace_id),
            )

    def _insert_span(
        self, cur: Any, trace_id: str, workspace_id: str, index: int, span: Span
    ) -> None:
        cur.execute(
            """
            INSERT INTO trace_spans
              (span_id, trace_id, workspace_id, span_index, kind, parent_id, name,
               started_at, duration_ms)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                span.id,
                trace_id,
                workspace_id,
                index,
                span.kind.value,
                span.parent_id,
                span.name,
                span.started_at,
                span.duration_ms,
            ),
        )
        if isinstance(span, LLMCallSpan):
            cur.execute(
                """
                INSERT INTO trace_llm_calls
                  (span_id, trace_id, workspace_id, provider, model, model_version_pinned,
                   system_prompt_pointer, system_prompt_hash, system_prompt_inline,
                   messages_pointer, messages_hash, messages_inline, tools_offered,
                   tools_offered_hash, params, reasoning_available, reasoning_redacted,
                   reasoning_summary_pointer, reasoning_full_pointer, reasoning_thinking_tokens,
                   reasoning_signature, output_content_pointer, output_content_hash,
                   output_content_inline, output_stop_reason, tokens_input,
                   tokens_input_cache_read, tokens_input_cache_creation, tokens_output,
                   tokens_reasoning, tokens_total, cost_input, cost_cache_read,
                   cost_cache_creation, cost_output, cost_total, time_to_first_token_ms,
                   tokens_per_second, retries, cache_hit, provider_metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    span.id,
                    trace_id,
                    workspace_id,
                    span.provider,
                    span.model,
                    span.model_version_pinned,
                    span.system_prompt_pointer,
                    span.system_prompt_hash,
                    span.system_prompt_inline,
                    span.messages_pointer,
                    span.messages_hash,
                    span.messages_inline,
                    list(span.tools_offered),
                    span.tools_offered_hash,
                    Jsonb(span.params),
                    span.reasoning.available,
                    span.reasoning.redacted,
                    span.reasoning.summary_pointer,
                    span.reasoning.full_pointer,
                    span.reasoning.thinking_tokens,
                    span.reasoning.signature,
                    span.output.content_pointer,
                    span.output.content_hash,
                    span.output.content_inline,
                    span.output.stop_reason.value if span.output.stop_reason else None,
                    span.tokens.input,
                    span.tokens.input_cache_read,
                    span.tokens.input_cache_creation,
                    span.tokens.output,
                    span.tokens.reasoning,
                    span.tokens.total,
                    span.cost_usd.input,
                    span.cost_usd.cache_read,
                    span.cost_usd.cache_creation,
                    span.cost_usd.output,
                    span.cost_usd.total,
                    span.time_to_first_token_ms,
                    span.tokens_per_second,
                    span.retries,
                    span.cache_hit,
                    Jsonb(span.provider_metadata),
                ),
            )
            for pos, req in enumerate(span.output.tool_use_requested):
                cur.execute(
                    "INSERT INTO trace_llm_tool_requests "
                    "(trace_id, span_id, position, tool, tool_use_id) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (trace_id, span.id, pos, req.tool, req.tool_use_id),
                )
        elif isinstance(span, ToolCallSpan):
            cur.execute(
                """
                INSERT INTO trace_tool_calls
                  (span_id, trace_id, workspace_id, tool_name, tool_version, tool_use_id,
                   args_pointer, args_hash, result_pointer, result_hash, status, error,
                   retry_chain, sandboxed, side_effects)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    span.id,
                    trace_id,
                    workspace_id,
                    span.tool_name,
                    span.tool_version,
                    span.tool_use_id,
                    span.args_pointer,
                    span.args_hash,
                    span.result_pointer,
                    span.result_hash,
                    span.status.value,
                    span.error,
                    list(span.retry_chain),
                    span.sandboxed,
                    Jsonb(span.side_effects),
                ),
            )
        elif isinstance(span, RetrievalSpan):
            cur.execute(
                """
                INSERT INTO trace_retrieval_spans
                  (trace_id, span_id, retriever, query_pointer, query_hash,
                   query_embedding_model, top_k_requested, top_k_returned, reranker,
                   grounding_used)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    trace_id,
                    span.id,
                    span.retriever,
                    span.query_pointer,
                    span.query_hash,
                    span.query_embedding_model,
                    span.top_k_requested,
                    span.top_k_returned,
                    span.reranker,
                    list(span.grounding_used),
                ),
            )
            for pos, doc in enumerate(span.retrieved):
                cur.execute(
                    """
                    INSERT INTO trace_retrieved_docs
                      (trace_id, span_id, position, doc_id, doc_version, chunk_id,
                       raw_score, rerank_score)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        trace_id,
                        span.id,
                        pos,
                        doc.doc_id,
                        doc.doc_version,
                        doc.chunk_id,
                        doc.raw_score,
                        doc.rerank_score,
                    ),
                )
        elif isinstance(span, MemoryReadSpan):
            cur.execute(
                """
                INSERT INTO trace_memory_read_spans
                  (trace_id, span_id, memory_store, keys_requested, keys_hit, keys_missed,
                   values_pointer)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    trace_id,
                    span.id,
                    span.memory_store,
                    list(span.keys_requested),
                    list(span.keys_hit),
                    list(span.keys_missed),
                    span.values_pointer,
                ),
            )
        elif isinstance(span, MemoryWriteSpan):
            cur.execute(
                "INSERT INTO trace_memory_write_spans "
                "(trace_id, span_id, memory_store, keys_written, values_pointer) "
                "VALUES (%s, %s, %s, %s, %s)",
                (trace_id, span.id, span.memory_store, list(span.keys_written), span.values_pointer),
            )
        elif isinstance(span, DecisionSpan):
            cur.execute(
                """
                INSERT INTO trace_decision_spans
                  (trace_id, span_id, decision_type, chosen, alternatives_considered,
                   rationale_pointer, confidence)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    trace_id,
                    span.id,
                    span.decision_type,
                    span.chosen,
                    list(span.alternatives_considered),
                    span.rationale_pointer,
                    span.confidence,
                ),
            )
        elif isinstance(span, HandoffSpan):
            cur.execute(
                "INSERT INTO trace_handoff_spans (trace_id, span_id, target, payload_pointer) "
                "VALUES (%s, %s, %s, %s)",
                (trace_id, span.id, span.target, span.payload_pointer),
            )
        elif isinstance(span, HumanInterventionSpan):
            cur.execute(
                "INSERT INTO trace_human_intervention_spans "
                "(trace_id, span_id, actor, action, rationale_pointer) VALUES (%s, %s, %s, %s, %s)",
                (trace_id, span.id, span.actor, span.action, span.rationale_pointer),
            )
        elif isinstance(span, GuardrailCheckSpan):
            cur.execute(
                "INSERT INTO trace_guardrail_check_spans "
                "(trace_id, span_id, guardrail, passed, detail_pointer) VALUES (%s, %s, %s, %s, %s)",
                (trace_id, span.id, span.guardrail, span.passed, span.detail_pointer),
            )
        elif isinstance(span, ErrorSpan):
            cur.execute(
                "INSERT INTO trace_error_spans "
                "(trace_id, span_id, error_type, message, recoverable) "
                "VALUES (%s, %s, %s, %s, %s)",
                (trace_id, span.id, span.error_type, span.message, span.recoverable),
            )
        elif isinstance(span, CustomSpan):
            cur.execute(
                "INSERT INTO trace_custom_spans (trace_id, span_id, payload) VALUES (%s, %s, %s)",
                (trace_id, span.id, Jsonb(span.payload)),
            )
        # AgentTurnSpan: no detail table.

    def load(self, cur: Any, workspace_id: str, entity_id: str) -> Trace | None:
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
    ) -> list[Trace]:
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

    def _build(self, cur: Any, row: tuple[Any, ...]) -> Trace:
        d = dict(zip(_ALL_COLUMNS, row, strict=True))
        tid = d["id"]
        spans = self._load_spans(cur, tid)
        cur.execute(
            """
            SELECT grader, label, score, reason, reason_pointer, confidence,
                   failure_modes, breakdown
            FROM trace_grader_results WHERE trace_id = %s ORDER BY result_index
            """,
            (tid,),
        )
        grader_results = [
            GraderResult(
                grader=g,
                label=lbl,
                score=sc,
                reason=rs,
                reason_pointer=rp,
                confidence=cf,
                failure_modes=fm,
                breakdown=bd,
            )
            for g, lbl, sc, rs, rp, cf, fm, bd in cur.fetchall()
        ]
        cur.execute(
            "SELECT kind, target_trace_id FROM trace_links WHERE trace_id = %s ORDER BY position",
            (tid,),
        )
        links = [TraceLink(kind=k, trace_id=tt) for k, tt in cur.fetchall()]
        return Trace(
            id=d["id"],
            workspace_id=d["workspace_id"],
            version=d["version"],
            created_at=d["created_at"],
            updated_at=d["updated_at"],
            deleted_at=d["deleted_at"],
            schema_version=d["schema_version"],
            snapshot_id=d["snapshot_id"],
            run=RunInfo(
                run_id=d["run_id"],
                experiment_id=d["run_experiment_id"],
                iteration=d["run_iteration"],
                variant_id=d["run_variant_id"],
                eval_case_id=d["run_eval_case_id"],
                repetition=d["run_repetition"],
                seed=d["run_seed"],
                thread_id=d["run_thread_id"],
                thread_position=d["run_thread_position"],
            ),
            agent=AgentSnapshotRef(
                fleet_version=d["agent_fleet_version"],
                agent_id=d["agent_agent_id"],
                agent_version=d["agent_agent_version"],
                parameters_snapshot_id=d["agent_parameters_snapshot_id"],
            ),
            environment=EnvironmentInfo(
                framework_version=d["env_framework_version"],
                runtime=d["env_runtime"],
                sandbox=d["env_sandbox"],
                tool_mocks=d["env_tool_mocks"],
                started_at=d["env_started_at"],
                ended_at=d["env_ended_at"],
            ),
            final_state=FinalState(status=d["final_state_status"], error=d["final_state_error"]),
            spans=spans,
            outputs=TraceOutputs(
                final_response_pointer=d["outputs_final_response_pointer"],
                structured_output=d["outputs_structured_output"],
            ),
            grader_results=grader_results,
            metrics=TraceMetrics(
                total_tokens_in=d["metrics_total_tokens_in"],
                total_tokens_out=d["metrics_total_tokens_out"],
                total_cost_usd=d["metrics_total_cost_usd"],
                total_duration_ms=d["metrics_total_duration_ms"],
                tool_call_count=d["metrics_tool_call_count"],
                llm_call_count=d["metrics_llm_call_count"],
                retries=d["metrics_retries"],
                recovery_events=d["metrics_recovery_events"],
                loop_detected=d["metrics_loop_detected"],
            ),
            links=links,
        )

    def _load_spans(self, cur: Any, trace_id: str) -> list[Span]:
        cur.execute(
            """
            SELECT span_id, kind, parent_id, name, started_at, duration_ms
            FROM trace_spans WHERE trace_id = %s ORDER BY span_index
            """,
            (trace_id,),
        )
        base_rows = cur.fetchall()
        spans: list[Span] = []
        for span_id, kind, parent_id, name, started_at, duration_ms in base_rows:
            base = {
                "id": span_id,
                "parent_id": parent_id,
                "name": name,
                "started_at": started_at,
                "duration_ms": duration_ms,
            }
            spans.append(self._build_span(cur, trace_id, kind, span_id, base))
        return spans

    def _build_span(self, cur: Any, trace_id: str, kind: str, span_id: str, base: dict[str, Any]) -> Span:
        if kind == "agent_turn":
            return AgentTurnSpan(**base)
        if kind == "llm_call":
            return self._build_llm_span(cur, trace_id, span_id, base)
        if kind == "tool_call":
            return self._build_tool_span(cur, trace_id, span_id, base)
        if kind == "retrieval":
            return self._build_retrieval_span(cur, trace_id, span_id, base)
        if kind == "memory_read":
            cur.execute(
                "SELECT memory_store, keys_requested, keys_hit, keys_missed, values_pointer "
                "FROM trace_memory_read_spans WHERE trace_id = %s AND span_id = %s",
                (trace_id, span_id),
            )
            store, req, hit, missed, vp = cur.fetchone()
            return MemoryReadSpan(
                **base,
                memory_store=store,
                keys_requested=req,
                keys_hit=hit,
                keys_missed=missed,
                values_pointer=vp,
            )
        if kind == "memory_write":
            cur.execute(
                "SELECT memory_store, keys_written, values_pointer "
                "FROM trace_memory_write_spans WHERE trace_id = %s AND span_id = %s",
                (trace_id, span_id),
            )
            store, written, vp = cur.fetchone()
            return MemoryWriteSpan(
                **base, memory_store=store, keys_written=written, values_pointer=vp
            )
        if kind == "decision":
            cur.execute(
                "SELECT decision_type, chosen, alternatives_considered, rationale_pointer, "
                "confidence FROM trace_decision_spans WHERE trace_id = %s AND span_id = %s",
                (trace_id, span_id),
            )
            dt, chosen, alts, rp, conf = cur.fetchone()
            return DecisionSpan(
                **base,
                decision_type=dt,
                chosen=chosen,
                alternatives_considered=alts,
                rationale_pointer=rp,
                confidence=conf,
            )
        if kind == "handoff":
            cur.execute(
                "SELECT target, payload_pointer FROM trace_handoff_spans WHERE trace_id = %s AND span_id = %s",
                (trace_id, span_id),
            )
            target, pp = cur.fetchone()
            return HandoffSpan(**base, target=target, payload_pointer=pp)
        if kind == "human_intervention":
            cur.execute(
                "SELECT actor, action, rationale_pointer "
                "FROM trace_human_intervention_spans WHERE trace_id = %s AND span_id = %s",
                (trace_id, span_id),
            )
            actor, action, rp = cur.fetchone()
            return HumanInterventionSpan(**base, actor=actor, action=action, rationale_pointer=rp)
        if kind == "guardrail_check":
            cur.execute(
                "SELECT guardrail, passed, detail_pointer "
                "FROM trace_guardrail_check_spans WHERE trace_id = %s AND span_id = %s",
                (trace_id, span_id),
            )
            guardrail, passed, dp = cur.fetchone()
            return GuardrailCheckSpan(**base, guardrail=guardrail, passed=passed, detail_pointer=dp)
        if kind == "error":
            cur.execute(
                "SELECT error_type, message, recoverable FROM trace_error_spans WHERE trace_id = %s AND span_id = %s",
                (trace_id, span_id),
            )
            et, msg, rec = cur.fetchone()
            return ErrorSpan(**base, error_type=et, message=msg, recoverable=rec)
        if kind == "custom":
            cur.execute(
                "SELECT payload FROM trace_custom_spans WHERE trace_id = %s AND span_id = %s",
                (trace_id, span_id),
            )
            (payload,) = cur.fetchone()
            return CustomSpan(**base, payload=payload)
        raise ValueError(f"unknown span kind {kind!r}")

    def _build_llm_span(self, cur: Any, trace_id: str, span_id: str, base: dict[str, Any]) -> LLMCallSpan:
        cur.execute(
            """
            SELECT provider, model, model_version_pinned, system_prompt_pointer,
                   system_prompt_hash, system_prompt_inline, messages_pointer, messages_hash,
                   messages_inline, tools_offered, tools_offered_hash, params,
                   reasoning_available, reasoning_redacted, reasoning_summary_pointer,
                   reasoning_full_pointer, reasoning_thinking_tokens, reasoning_signature,
                   output_content_pointer, output_content_hash, output_content_inline,
                   output_stop_reason, tokens_input, tokens_input_cache_read,
                   tokens_input_cache_creation, tokens_output, tokens_reasoning, tokens_total,
                   cost_input, cost_cache_read, cost_cache_creation, cost_output, cost_total,
                   time_to_first_token_ms, tokens_per_second, retries, cache_hit, provider_metadata
            FROM trace_llm_calls WHERE trace_id = %s AND span_id = %s
            """,
            (trace_id, span_id),
        )
        r = cur.fetchone()
        cur.execute(
            "SELECT tool, tool_use_id FROM trace_llm_tool_requests "
            "WHERE trace_id = %s AND span_id = %s ORDER BY position",
            (trace_id, span_id),
        )
        tool_use_requested = [ToolUseRequest(tool=t, tool_use_id=tid) for t, tid in cur.fetchall()]
        return LLMCallSpan(
            **base,
            provider=r[0],
            model=r[1],
            model_version_pinned=r[2],
            system_prompt_pointer=r[3],
            system_prompt_hash=r[4],
            system_prompt_inline=r[5],
            messages_pointer=r[6],
            messages_hash=r[7],
            messages_inline=r[8],
            tools_offered=r[9],
            tools_offered_hash=r[10],
            params=r[11],
            reasoning=ReasoningBlock(
                available=r[12],
                redacted=r[13],
                summary_pointer=r[14],
                full_pointer=r[15],
                thinking_tokens=r[16],
                signature=r[17],
            ),
            output=LLMOutput(
                content_pointer=r[18],
                content_hash=r[19],
                content_inline=r[20],
                stop_reason=r[21],
                tool_use_requested=tool_use_requested,
            ),
            tokens=TokenBreakdown(
                input=r[22],
                input_cache_read=r[23],
                input_cache_creation=r[24],
                output=r[25],
                reasoning=r[26],
                total=r[27],
            ),
            cost_usd=CostBreakdown(
                input=r[28],
                cache_read=r[29],
                cache_creation=r[30],
                output=r[31],
                total=r[32],
            ),
            time_to_first_token_ms=r[33],
            tokens_per_second=r[34],
            retries=r[35],
            cache_hit=r[36],
            provider_metadata=r[37],
        )

    def _build_tool_span(self, cur: Any, trace_id: str, span_id: str, base: dict[str, Any]) -> ToolCallSpan:
        cur.execute(
            """
            SELECT tool_name, tool_version, tool_use_id, args_pointer, args_hash,
                   result_pointer, result_hash, status, error, retry_chain, sandboxed, side_effects
            FROM trace_tool_calls WHERE trace_id = %s AND span_id = %s
            """,
            (trace_id, span_id),
        )
        r = cur.fetchone()
        return ToolCallSpan(
            **base,
            tool_name=r[0],
            tool_version=r[1],
            tool_use_id=r[2],
            args_pointer=r[3],
            args_hash=r[4],
            result_pointer=r[5],
            result_hash=r[6],
            status=r[7],
            error=r[8],
            retry_chain=r[9],
            sandboxed=r[10],
            side_effects=r[11],
        )

    def _build_retrieval_span(self, cur: Any, trace_id: str, span_id: str, base: dict[str, Any]) -> RetrievalSpan:
        cur.execute(
            """
            SELECT retriever, query_pointer, query_hash, query_embedding_model,
                   top_k_requested, top_k_returned, reranker, grounding_used
            FROM trace_retrieval_spans WHERE trace_id = %s AND span_id = %s
            """,
            (trace_id, span_id),
        )
        r = cur.fetchone()
        cur.execute(
            "SELECT doc_id, doc_version, chunk_id, raw_score, rerank_score "
            "FROM trace_retrieved_docs WHERE trace_id = %s AND span_id = %s ORDER BY position",
            (trace_id, span_id),
        )
        retrieved = [
            RetrievedDoc(
                doc_id=doc_id,
                doc_version=dv,
                chunk_id=cid,
                raw_score=rs,
                rerank_score=rr,
            )
            for doc_id, dv, cid, rs, rr in cur.fetchall()
        ]
        return RetrievalSpan(
            **base,
            retriever=r[0],
            query_pointer=r[1],
            query_hash=r[2],
            query_embedding_model=r[3],
            top_k_requested=r[4],
            top_k_returned=r[5],
            reranker=r[6],
            retrieved=retrieved,
            grounding_used=r[7],
        )


register_mapper(TraceMapper())
