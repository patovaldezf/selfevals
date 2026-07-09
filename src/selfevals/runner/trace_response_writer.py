"""Free functions for `Executor._record_response` — write an AdapterResponse to a trace.

Split out of `executor.py` (same free-function-delegate pattern as
`storage/postgres/mappers/trace_spans_write.py`): pure functions that take
the recorder/response/request plus the collaborators they need (sandbox,
adapter, payload routing), with no `self`. `Executor` stays the single
call site.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from selfevals.runner.pricing import estimate_cost
from selfevals.schemas.enums import StopReason, ToolCallStatus
from selfevals.schemas.trace import (
    INLINE_PAYLOAD_MAX_CHARS,
    CostBreakdown,
    TokenBreakdown,
    ToolUseRequest,
    TraceOutputs,
)

if TYPE_CHECKING:
    from selfevals.runner.adapters import AdapterRequest, AdapterResponse, AgentAdapter
    from selfevals.runner.sandbox import SandboxPolicy
    from selfevals.trace.recorder import TraceRecorder

_STOP_REASON_NORMALIZE: dict[str, StopReason] = {sr.value: sr for sr in StopReason}


def _normalize_stop_reason(value: str | None) -> StopReason | None:
    if value is None:
        return None
    return _STOP_REASON_NORMALIZE.get(value.strip().lower())


def _optional_int(value: object) -> int | None:
    """Coerce a provider-metadata timing value to a non-negative int, or None.

    Adapters put streaming timings into `provider_metadata` as plain JSON; be
    forgiving about numeric types but reject anything that is not a usable
    non-negative number.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return int(value) if value >= 0 else None
    return None


def _optional_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value) if value >= 0 else None
    return None


def _str_or_none(value: object) -> str | None:
    """A non-empty string, or None. Used to read provider/model from the
    adapter's free-form `provider_metadata` without trusting its types."""
    if isinstance(value, str) and value.strip():
        return value
    return None


def _extract_system_prompt(request: AdapterRequest) -> str | None:
    """Pull a system prompt out of the proposer envelope, if one is there.

    The editable contract routes prompt overrides through
    `parameters["model_params"]`; a proposer that swaps the system prompt puts it
    under a `system_prompt` (or `system`) key. None when the run carries no
    explicit system prompt — we never fabricate one."""
    inner = (request.parameters or {}).get("model_params") or {}
    for key in ("system_prompt", "system"):
        candidate = inner.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    return None


def route_payload(
    recorder: TraceRecorder, key: str, value: Any
) -> tuple[str | None, str | None, str | None]:
    """Decide how to persist one trace payload: pointer, inline, or both.

    Returns `(pointer, hash, inline)`. Small payloads are inlined on the span
    (so the viewer needs no extra fetch); large ones are offloaded to the
    object store via the recorder's `PayloadRouter` and referenced by
    pointer. Without a router (e.g. `--no-persist`) we only inline, truncated
    to `INLINE_PAYLOAD_MAX_CHARS` so a chatty run can't bloat the trace. A
    None/empty value routes to all-None (honest: nothing to show)."""
    if value is None:
        return None, None, None
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    inline = text if len(text) <= INLINE_PAYLOAD_MAX_CHARS else text[:INLINE_PAYLOAD_MAX_CHARS]
    router = recorder.payload_router
    if router is None:
        return None, None, inline
    routed = router.route_value(key, value)
    # Offloaded → pointer + hash, keep the inline preview too. Inlined by the
    # router (small) → no pointer; the inline text already carries it.
    if routed.pointer is not None:
        return routed.pointer, routed.content_hash, inline
    return None, routed.content_hash, inline


def cost_for(adapter: AgentAdapter, response: AdapterResponse) -> CostBreakdown | None:
    """Resolve the cost of one adapter response.

    The adapter's `cost_usd` is authoritative when it reports one — we keep
    it as the breakdown total (the component split is the provider's, not
    ours to infer). When the adapter reports no cost, derive it from the
    response tokens and the known model (the spec-declared `agent.model` for
    cli/http, or the agent record) via the pricing table. No known model
    yields None (a one-time warning fires in the pricing layer) — we never
    fabricate a cost. This is why a cli/http agent that returns tokens but no
    cost still showed `$0.00`: without a declared model there's nothing to
    price against.
    """
    if response.cost_usd > 0:
        return CostBreakdown(total=response.cost_usd)
    model = adapter.model or (adapter.agent.model if adapter.agent else None)
    if model is None:
        return None
    tokens = TokenBreakdown(
        input=response.tokens_input,
        input_cache_read=response.tokens_cache_read,
        input_cache_creation=response.tokens_cache_creation,
        output=response.tokens_output,
        reasoning=response.tokens_reasoning,
        total=(
            response.tokens_input
            + response.tokens_cache_read
            + response.tokens_cache_creation
            + response.tokens_output
            + response.tokens_reasoning
        ),
    )
    return estimate_cost(model.provider, model.name, tokens)


def _write_tool_spans(
    recorder: TraceRecorder,
    response: AdapterResponse,
    sandbox: SandboxPolicy,
) -> None:
    for tu in response.tool_uses:
        # The Tool registry does not yet annotate side-effects, so we treat
        # every tool as side-effect-free for sandbox-mocking decisions.
        side_effects = False
        sandboxed = sandbox.should_mock_tool(side_effects=side_effects)
        with recorder.tool_call(
            tu.tool,
            tool_name=tu.tool,
            tool_use_id=tu.tool_use_id,
        ) as tool_span:
            tool_span.sandboxed = sandboxed
            tool_span.status = ToolCallStatus.OK
            # Record what the tool was called with, so the trace shows the
            # tool args, not just that a tool fired.
            if tu.args:
                args_ptr, args_hash, _inline = route_payload(
                    recorder, f"tool_args:{tu.tool}", tu.args
                )
                tool_span.args_pointer = args_ptr
                tool_span.args_hash = args_hash


def record_adapter_response(
    recorder: TraceRecorder,
    response: AdapterResponse,
    request: AdapterRequest,
    *,
    adapter: AgentAdapter,
    sandbox: SandboxPolicy,
) -> None:
    """Write an `AdapterResponse` to `recorder` as an LLM call span + tool spans."""
    # The model name reported by the agent wins (embedded specs declare no
    # model — the function does), then the spec-declared `agent.model`
    # (cli/http), then the agent record, then "unknown". This is what stops
    # the trace viewer showing model="unknown".
    meta = response.provider_metadata
    declared = adapter.model
    agent_model = adapter.agent.model if adapter.agent else None
    provider = (
        _str_or_none(meta.get("provider"))
        or (declared.provider if declared else None)
        or (agent_model.provider if agent_model else None)
    )
    model = (
        _str_or_none(meta.get("model"))
        or (declared.name if declared else None)
        or (agent_model.name if agent_model else None)
    )
    with recorder.llm_call(
        "adapter_response",
        provider=provider or "unknown",
        model=model or "unknown",
    ) as llm:
        llm.add_tokens(
            input=response.tokens_input,
            input_cache_read=response.tokens_cache_read,
            input_cache_creation=response.tokens_cache_creation,
            output=response.tokens_output,
            reasoning=response.tokens_reasoning,
        )
        # Capture the prompt side: the input messages and the system prompt
        # (when the proposer/case supplies one) so the trace shows what the
        # model was actually asked, not just what it answered.
        messages_ptr, messages_hash, messages_inline = route_payload(
            recorder, "messages", request.input
        )
        llm.messages_pointer = messages_ptr
        llm.messages_hash = messages_hash
        llm.messages_inline = messages_inline
        system_prompt = _extract_system_prompt(request)
        if system_prompt is not None:
            sys_ptr, sys_hash, sys_inline = route_payload(recorder, "system_prompt", system_prompt)
            llm.system_prompt_pointer = sys_ptr
            llm.system_prompt_hash = sys_hash
            llm.system_prompt_inline = sys_inline
        llm.tools_offered = list(request.tools_allowed)
        # Capture the response side: the answer text, inlined when small and
        # offloaded to the object store when large.
        content_ptr, content_hash, content_inline = route_payload(
            recorder, "content", response.content
        )
        tool_use_requests = [
            ToolUseRequest(tool=tu.tool, tool_use_id=tu.tool_use_id) for tu in response.tool_uses
        ]
        llm.set_output(
            stop_reason=_normalize_stop_reason(response.stop_reason),
            content_pointer=content_ptr,
            content_hash=content_hash,
            content_inline=content_inline,
            tool_use_requested=tool_use_requests,
        )
        llm.set_timing(
            time_to_first_token_ms=_optional_int(
                response.provider_metadata.get("time_to_first_token_ms")
            ),
            tokens_per_second=_optional_float(response.provider_metadata.get("tokens_per_second")),
        )
        llm.set_cost(cost_for(adapter, response))
        llm.provider_metadata = dict(response.provider_metadata)
    # Surface the structured output on the trace so the FE can show the
    # detected structured payload alongside the text answer.
    if response.structured_output is not None:
        recorder.set_outputs(TraceOutputs(structured_output=response.structured_output))
    _write_tool_spans(recorder, response, sandbox)
