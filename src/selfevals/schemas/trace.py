"""Trace schema — the canonical record of one agent run.

A Trace captures everything selfevals needs to:
- replay a run (snapshots + content hashes for inputs)
- attribute cost/latency (token breakdown + per-call timing)
- score outcomes (grader results + final outputs)
- debug failures (per-span error chain, retry chain, side effects)
- link related runs (paraphrase variant, replay-of)

Spans are a discriminated union on `kind`. Each kind has its own payload
fields. Long payloads (system prompts, tool args, retrieval results, reasoning
summaries) are referenced by `*_pointer` + recorded by `*_hash` rather than
inlined, so that traces stay small and content-addressed.

Key contracts enforced here:
- `kind` is the discriminator; subclasses must match the literal.
- Every `ToolCallSpan` with a `tool_use_id` must have a corresponding
  `LLMCallSpan` somewhere in the trace whose `output.tool_use_requested`
  contains that id. Enforced as a trace-level model_validator.
- Token counts: `tokens.total >= input + input_cache_read +
  input_cache_creation + output + reasoning` (loose lower bound; providers
  may report differently, so we use >= not ==).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, ClassVar, Literal

from pydantic import Discriminator, Field, model_validator

from selfevals.schemas._base import BaseEntity, NonEmptyStr, SelfEvalsModel
from selfevals.schemas.enums import (
    SandboxMode,
    SpanKind,
    StopReason,
    ToolCallStatus,
    TraceState,
)

# Schema changelog (all additive — old traces load unchanged):
# 1.1.0: `CostBreakdown.cache_creation` (cache-write cost line).
# 1.2.0: `GraderResult.breakdown` — optional funnel breakdown tree
#        (see graders.base.BreakdownNode); default None when absent.
# 1.3.0: `LLMCallSpan.{system_prompt_inline,messages_inline}` +
#        `LLMOutput.content_inline` — small payloads inlined on the span so the
#        trace viewer shows the prompt/response without resolving a pointer.
#        Large payloads still go to the object store via the matching `*_pointer`.
TRACE_SCHEMA_VERSION = "1.3.0"

# Inlined trace payloads are capped so a chatty run can't bloat the Trace row.
# Anything larger is offloaded to the object store and referenced by pointer.
INLINE_PAYLOAD_MAX_CHARS = 4096


class RunInfo(SelfEvalsModel):
    run_id: NonEmptyStr
    experiment_id: NonEmptyStr | None = None
    iteration: int | None = Field(default=None, ge=0)
    variant_id: NonEmptyStr | None = None
    eval_case_id: NonEmptyStr | None = None
    repetition: int = Field(default=0, ge=0)
    seed: int | None = None
    thread_id: NonEmptyStr | None = None
    """Groups multiple traces that belong to one conversation / session.

    A multi-turn eval case produces one Trace per turn; they all share a
    `thread_id` so the traces can be assembled back into the ordered thread.
    Sourced from OTel `session.id` / `gen_ai.conversation.id` on import, or
    set explicitly by the caller."""
    thread_position: int | None = Field(default=None, ge=0)
    """0-based turn index within the thread. None when the run is standalone
    (no thread) or when ordering is to be inferred from `started_at`."""


class AgentSnapshotRef(SelfEvalsModel):
    fleet_version: int | None = Field(default=None, ge=1)
    agent_id: NonEmptyStr
    agent_version: int = Field(ge=1)
    parameters_snapshot_id: NonEmptyStr | None = None


class EnvironmentInfo(SelfEvalsModel):
    framework_version: NonEmptyStr
    runtime: NonEmptyStr
    sandbox: SandboxMode
    tool_mocks: list[NonEmptyStr] = Field(default_factory=list)
    started_at: datetime
    ended_at: datetime | None = None

    @model_validator(mode="after")
    def _ended_after_started(self) -> EnvironmentInfo:
        if self.ended_at is not None and self.ended_at < self.started_at:
            raise ValueError("environment.ended_at must be >= started_at")
        return self


class FinalState(SelfEvalsModel):
    status: TraceState
    error: str | None = None


class ReasoningBlock(SelfEvalsModel):
    """Extended-thinking metadata from the model. Operational §B.2."""

    available: bool = False
    redacted: bool = False
    summary_pointer: str | None = None
    full_pointer: str | None = None
    thinking_tokens: int = Field(default=0, ge=0)
    signature: str | None = None
    """Opaque provider signature used for replay/integrity. Stored verbatim."""


class TokenBreakdown(SelfEvalsModel):
    """Per-call token accounting. Operational §B.2 cache token breakdown."""

    input: int = Field(default=0, ge=0)
    input_cache_read: int = Field(default=0, ge=0)
    input_cache_creation: int = Field(default=0, ge=0)
    output: int = Field(default=0, ge=0)
    reasoning: int = Field(default=0, ge=0)
    total: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _total_covers_components(self) -> TokenBreakdown:
        component_sum = (
            self.input
            + self.input_cache_read
            + self.input_cache_creation
            + self.output
            + self.reasoning
        )
        # Providers may double-count or omit; we only assert the lower bound.
        if self.total < component_sum:
            raise ValueError(
                f"tokens.total ({self.total}) must be >= sum of components ({component_sum})"
            )
        return self


class CostBreakdown(SelfEvalsModel):
    input: float = Field(default=0.0, ge=0.0)
    cache_read: float = Field(default=0.0, ge=0.0)
    cache_creation: float = Field(default=0.0, ge=0.0)
    output: float = Field(default=0.0, ge=0.0)
    total: float = Field(default=0.0, ge=0.0)


class ToolUseRequest(SelfEvalsModel):
    tool: NonEmptyStr
    tool_use_id: NonEmptyStr


class LLMOutput(SelfEvalsModel):
    content_pointer: str | None = None
    content_hash: str | None = None
    content_inline: str | None = None
    """The model's response text, inlined when small (<= INLINE_PAYLOAD_MAX_CHARS).
    Lets the trace viewer show the answer directly; larger content lives in the
    object store behind `content_pointer`. Both may be set when truncated."""
    stop_reason: StopReason | None = None
    tool_use_requested: list[ToolUseRequest] = Field(default_factory=list)


class RetrievedDoc(SelfEvalsModel):
    doc_id: NonEmptyStr
    doc_version: str | None = None
    chunk_id: str | None = None
    raw_score: float | None = None
    rerank_score: float | None = None


class TraceLink(SelfEvalsModel):
    kind: Literal["paraphrase_variant", "replay_of", "spawned_by"]
    trace_id: NonEmptyStr


class TraceMetrics(SelfEvalsModel):
    total_tokens_in: int = Field(default=0, ge=0)
    total_tokens_out: int = Field(default=0, ge=0)
    total_cost_usd: float = Field(default=0.0, ge=0.0)
    total_duration_ms: int = Field(default=0, ge=0)
    tool_call_count: int = Field(default=0, ge=0)
    llm_call_count: int = Field(default=0, ge=0)
    retries: int = Field(default=0, ge=0)
    recovery_events: int = Field(default=0, ge=0)
    loop_detected: bool = False


class _SpanBase(SelfEvalsModel):
    id: NonEmptyStr
    parent_id: NonEmptyStr | None = None
    name: NonEmptyStr
    started_at: datetime
    duration_ms: int = Field(default=0, ge=0)


class AgentTurnSpan(_SpanBase):
    kind: Literal[SpanKind.AGENT_TURN] = SpanKind.AGENT_TURN


class LLMCallSpan(_SpanBase):
    kind: Literal[SpanKind.LLM_CALL] = SpanKind.LLM_CALL
    provider: NonEmptyStr
    model: NonEmptyStr
    model_version_pinned: str | None = None
    system_prompt_pointer: str | None = None
    system_prompt_hash: str | None = None
    system_prompt_inline: str | None = None
    """System prompt inlined when small; otherwise behind `system_prompt_pointer`."""
    messages_pointer: str | None = None
    messages_hash: str | None = None
    messages_inline: str | None = None
    """Input messages (the prompt sent to the model) inlined when small; otherwise
    behind `messages_pointer`. JSON-encoded string of the conversation payload."""
    tools_offered: list[NonEmptyStr] = Field(default_factory=list)
    tools_offered_hash: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    reasoning: ReasoningBlock = Field(default_factory=ReasoningBlock)
    output: LLMOutput = Field(default_factory=LLMOutput)
    tokens: TokenBreakdown = Field(default_factory=TokenBreakdown)
    cost_usd: CostBreakdown = Field(default_factory=CostBreakdown)
    time_to_first_token_ms: int | None = Field(default=None, ge=0)
    tokens_per_second: float | None = Field(default=None, ge=0.0)
    retries: int = Field(default=0, ge=0)
    cache_hit: bool = False
    provider_metadata: dict[str, Any] = Field(default_factory=dict)


class ToolCallSpan(_SpanBase):
    kind: Literal[SpanKind.TOOL_CALL] = SpanKind.TOOL_CALL
    tool_name: NonEmptyStr
    tool_version: str | None = None
    tool_use_id: str | None = None
    """Links back to a LLMCallSpan.output.tool_use_requested entry. Validated
    at trace level."""

    args_pointer: str | None = None
    args_hash: str | None = None
    result_pointer: str | None = None
    result_hash: str | None = None
    status: ToolCallStatus = ToolCallStatus.OK
    error: str | None = None
    retry_chain: list[str] = Field(default_factory=list)
    sandboxed: bool = False
    side_effects: dict[str, Any] = Field(default_factory=dict)


class RetrievalSpan(_SpanBase):
    kind: Literal[SpanKind.RETRIEVAL] = SpanKind.RETRIEVAL
    retriever: NonEmptyStr
    query_pointer: str | None = None
    query_hash: str | None = None
    query_embedding_model: str | None = None
    top_k_requested: int = Field(ge=1)
    top_k_returned: int = Field(default=0, ge=0)
    retrieved: list[RetrievedDoc] = Field(default_factory=list)
    reranker: str | None = None
    grounding_used: list[str] = Field(default_factory=list)


class MemoryReadSpan(_SpanBase):
    kind: Literal[SpanKind.MEMORY_READ] = SpanKind.MEMORY_READ
    memory_store: NonEmptyStr
    keys_requested: list[str] = Field(default_factory=list)
    keys_hit: list[str] = Field(default_factory=list)
    keys_missed: list[str] = Field(default_factory=list)
    values_pointer: str | None = None


class MemoryWriteSpan(_SpanBase):
    kind: Literal[SpanKind.MEMORY_WRITE] = SpanKind.MEMORY_WRITE
    memory_store: NonEmptyStr
    keys_written: list[str] = Field(default_factory=list)
    values_pointer: str | None = None


class DecisionSpan(_SpanBase):
    kind: Literal[SpanKind.DECISION] = SpanKind.DECISION
    decision_type: NonEmptyStr
    chosen: str
    alternatives_considered: list[str] = Field(default_factory=list)
    rationale_pointer: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class HandoffSpan(_SpanBase):
    kind: Literal[SpanKind.HANDOFF] = SpanKind.HANDOFF
    target: NonEmptyStr
    payload_pointer: str | None = None


class HumanInterventionSpan(_SpanBase):
    kind: Literal[SpanKind.HUMAN_INTERVENTION] = SpanKind.HUMAN_INTERVENTION
    actor: NonEmptyStr
    action: NonEmptyStr
    rationale_pointer: str | None = None


class GuardrailCheckSpan(_SpanBase):
    kind: Literal[SpanKind.GUARDRAIL_CHECK] = SpanKind.GUARDRAIL_CHECK
    guardrail: NonEmptyStr
    passed: bool
    detail_pointer: str | None = None


class ErrorSpan(_SpanBase):
    kind: Literal[SpanKind.ERROR] = SpanKind.ERROR
    error_type: NonEmptyStr
    message: str
    recoverable: bool = False


class CustomSpan(_SpanBase):
    kind: Literal[SpanKind.CUSTOM] = SpanKind.CUSTOM
    payload: dict[str, Any] = Field(default_factory=dict)


Span = Annotated[
    AgentTurnSpan
    | LLMCallSpan
    | ToolCallSpan
    | RetrievalSpan
    | MemoryReadSpan
    | MemoryWriteSpan
    | DecisionSpan
    | HandoffSpan
    | HumanInterventionSpan
    | GuardrailCheckSpan
    | ErrorSpan
    | CustomSpan,
    Discriminator("kind"),
]


class GraderResult(SelfEvalsModel):
    grader: NonEmptyStr
    label: str
    score: float | None = None
    reason: str | None = None
    """Inline free-text rationale the grader produced for this result. Small
    (<4KB); inlined directly so it persists with the trace and surfaces in
    reports. For large payloads use `reason_pointer` instead."""
    reason_pointer: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    failure_modes: list[NonEmptyStr] = Field(default_factory=list)
    """Stable failure-mode identities attributed to this result: deterministic
    grader tags at grade time, plus `FailureMode` ids stamped by error analysis
    (see error_analysis_design.md §4). The trace↔mode link lives here."""
    breakdown: dict[str, Any] | None = None
    """Optional funnel breakdown tree, serialized from a runtime
    `graders.base.BreakdownNode` (key/label/score/weight/reason/failure_modes/
    children). Additive and purely informational: the top-level `label`/`score`
    stay authoritative for pass/fail. Feeds the funnel drill-down."""


class TraceOutputs(SelfEvalsModel):
    final_response_pointer: str | None = None
    structured_output: dict[str, Any] | None = None


class Trace(BaseEntity):
    _id_prefix: ClassVar[str] = "tr"

    schema_version: str = TRACE_SCHEMA_VERSION
    run: RunInfo
    agent: AgentSnapshotRef
    environment: EnvironmentInfo
    final_state: FinalState
    spans: list[Span] = Field(default_factory=list)
    outputs: TraceOutputs = Field(default_factory=TraceOutputs)
    grader_results: list[GraderResult] = Field(default_factory=list)
    metrics: TraceMetrics = Field(default_factory=TraceMetrics)
    links: list[TraceLink] = Field(default_factory=list)
    snapshot_id: NonEmptyStr | None = None

    @model_validator(mode="after")
    def _span_ids_unique(self) -> Trace:
        ids = [s.id for s in self.spans]
        if len(set(ids)) != len(ids):
            raise ValueError("span ids must be unique within a Trace")
        return self

    @model_validator(mode="after")
    def _parent_ids_reference_existing_spans(self) -> Trace:
        ids = {s.id for s in self.spans}
        for s in self.spans:
            if s.parent_id is not None and s.parent_id not in ids:
                raise ValueError(
                    f"span {s.id!r} has parent_id={s.parent_id!r} which does "
                    "not exist in this Trace"
                )
        return self

    @model_validator(mode="after")
    def _tool_use_ids_linked(self) -> Trace:
        """Every ToolCallSpan.tool_use_id must be requested by some LLMCallSpan."""
        requested: set[str] = set()
        for s in self.spans:
            if isinstance(s, LLMCallSpan):
                for req in s.output.tool_use_requested:
                    requested.add(req.tool_use_id)
        for s in self.spans:
            if (
                isinstance(s, ToolCallSpan)
                and s.tool_use_id is not None
                and s.tool_use_id not in requested
            ):
                raise ValueError(
                    f"ToolCallSpan {s.id!r} references tool_use_id="
                    f"{s.tool_use_id!r}, but no LLMCallSpan in this Trace "
                    "requested it"
                )
        return self
