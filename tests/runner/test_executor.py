from __future__ import annotations

from typing import Any

import pytest

from selfevals.runner.adapters import (
    AdapterRequest,
    AdapterResponse,
    AdapterToolUse,
    EmbeddedAdapter,
)
from selfevals.runner.executor import Executor
from selfevals.runner.sandbox import SandboxPolicy
from selfevals.schemas.enums import (
    AgentType,
    DatasetSource,
    DatasetType,
    GroundTruthMethod,
    Level,
    SandboxMode,
    StopReason,
    ToolCallStatus,
)
from selfevals.schemas.eval_case import (
    CaseTaxonomy,
    EvalCase,
    Expected,
    FeatureTag,
    GroundTruthSpec,
    SourceInfo,
)
from selfevals.schemas.fleet import Agent, ModelRef
from selfevals.schemas.trace import AgentSnapshotRef, LLMCallSpan, RunInfo, ToolCallSpan
from selfevals.trace.recorder import TraceRecorder

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _case(**overrides: Any) -> EvalCase:
    base: dict[str, Any] = {
        "id": EvalCase.make_id(),
        "workspace_id": WS,
        "name": "echo",
        "task_type": "smoke",
        "input": {"messages": [{"role": "user", "content": "hi"}]},
        "taxonomy": CaseTaxonomy(
            level=Level.FINAL_RESPONSE,
            feature=FeatureTag(primary="commerce.product_resolution"),
            source=SourceInfo(type=DatasetSource.HANDCRAFTED),
            ground_truth=GroundTruthSpec(methods=[GroundTruthMethod.EXACT_MATCH]),
            dataset_type=DatasetType.CAPABILITY,
        ),
        "expected": Expected(must_include=["pong"]),
    }
    base.update(overrides)
    return EvalCase(**base)


def _agent() -> Agent:
    return Agent(
        id=Agent.make_id(),
        workspace_id=WS,
        agent_type=AgentType.SYSTEM_PROMPT,
        model=ModelRef(provider="anthropic", name="claude-sonnet-4-6"),
        system_prompt_pointer="oss://prompts/echo",
    )


def _ping(req: AdapterRequest) -> AdapterResponse:
    return AdapterResponse(
        content="pong",
        tokens_input=4,
        tokens_output=2,
        stop_reason="end_turn",
    )


def _tool_user(req: AdapterRequest) -> AdapterResponse:
    return AdapterResponse(
        content="ok",
        tokens_input=5,
        tokens_output=3,
        stop_reason="tool_use",
        tool_uses=[AdapterToolUse(tool="search", tool_use_id="toolu_01")],
    )


def _boom(req: AdapterRequest) -> AdapterResponse:
    raise RuntimeError("kaboom")


@pytest.mark.asyncio
async def test_executor_runs_one_repetition() -> None:
    agent = _agent()
    adapter = EmbeddedAdapter(_ping, agent=agent)
    executor = Executor(
        adapter=adapter,
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    run = await executor.run_case(_case())
    assert len(run.repetitions) == 1
    rep = run.repetitions[0]
    assert rep.error is None
    assert rep.response is not None
    assert rep.response.content == "pong"
    assert rep.trace.metrics.llm_call_count == 1
    # Ensure the agent turn and llm span are present.
    kinds = {type(s) for s in rep.trace.spans}
    assert LLMCallSpan in kinds


@pytest.mark.asyncio
async def test_executor_records_tool_calls_with_use_id_linkage() -> None:
    agent = _agent()
    executor = Executor(
        adapter=EmbeddedAdapter(_tool_user, agent=agent),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    run = await executor.run_case(_case())
    rep = run.repetitions[0]
    tool_spans = [s for s in rep.trace.spans if isinstance(s, ToolCallSpan)]
    assert len(tool_spans) == 1
    assert tool_spans[0].tool_use_id == "toolu_01"
    assert tool_spans[0].status == ToolCallStatus.OK
    assert tool_spans[0].sandboxed is True  # mock mode → always sandboxed


@pytest.mark.asyncio
async def test_executor_records_stop_reason() -> None:
    executor = Executor(
        adapter=EmbeddedAdapter(_ping, agent=_agent()),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    rep = (await executor.run_case(_case())).repetitions[0]
    llm_span = next(s for s in rep.trace.spans if isinstance(s, LLMCallSpan))
    assert llm_span.output.stop_reason == StopReason.END_TURN


@pytest.mark.asyncio
async def test_executor_marks_failed_repetition() -> None:
    executor = Executor(
        adapter=EmbeddedAdapter(_boom, agent=_agent()),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    run = await executor.run_case(_case())
    rep = run.repetitions[0]
    assert rep.error is not None
    assert "kaboom" in rep.error
    assert rep.response is None
    # Trace should reflect ERRORED final state.
    assert rep.trace.final_state.status.value == "errored"
    assert run.failed_count == 1
    assert run.successful_count == 0


@pytest.mark.asyncio
async def test_executor_runs_multiple_repetitions() -> None:
    executor = Executor(
        adapter=EmbeddedAdapter(_ping, agent=_agent()),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    run = await executor.run_case(_case(), repetitions=3)
    assert len(run.repetitions) == 3
    assert {r.repetition for r in run.repetitions} == {0, 1, 2}


@pytest.mark.asyncio
async def test_executor_rejects_invalid_repetitions() -> None:
    executor = Executor(
        adapter=EmbeddedAdapter(_ping, agent=_agent()),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    with pytest.raises(ValueError):
        await executor.run_case(_case(), repetitions=0)


def test_executor_rejects_invalid_concurrency() -> None:
    with pytest.raises(ValueError):
        Executor(
            adapter=EmbeddedAdapter(_ping, agent=_agent()),
            sandbox=SandboxPolicy(SandboxMode.MOCK),
            workspace_id=WS,
            concurrency=0,
        )


def test_executor_requires_workspace() -> None:
    with pytest.raises(ValueError):
        Executor(
            adapter=EmbeddedAdapter(_ping, agent=_agent()),
            sandbox=SandboxPolicy(SandboxMode.MOCK),
            workspace_id="",
        )


def test_executor_blocks_live_sandbox_at_construction() -> None:
    from selfevals.runner.sandbox import SandboxViolationError

    with pytest.raises(SandboxViolationError):
        Executor(
            adapter=EmbeddedAdapter(_ping, agent=_agent()),
            sandbox=SandboxPolicy(SandboxMode.LIVE_CANARY),
            workspace_id=WS,
        )


@pytest.mark.asyncio
async def test_executor_no_agent_still_produces_trace() -> None:
    # AgentSnapshotRef falls back to 'unknown' so adapters without an
    # attached Agent still produce a valid Trace.
    executor = Executor(
        adapter=EmbeddedAdapter(_ping),  # no agent attached
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    rep = (await executor.run_case(_case())).repetitions[0]
    assert rep.trace.agent.agent_id == "unknown"
    assert rep.error is None


@pytest.mark.asyncio
async def test_executor_repetitions_run_concurrently() -> None:
    import asyncio
    import time

    delay = 0.2

    async def slow(req: AdapterRequest) -> AdapterResponse:
        await asyncio.sleep(delay)
        return AdapterResponse(content="pong", stop_reason="end_turn")

    executor = Executor(
        adapter=EmbeddedAdapter(slow, agent=_agent()),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
        concurrency=8,
    )
    start = time.perf_counter()
    run = await executor.run_case(_case(), repetitions=5)
    elapsed = time.perf_counter() - start
    assert len(run.repetitions) == 5
    # 5 reps each sleeping `delay`; if sequential, elapsed >= 5*delay. Concurrent
    # execution should finish in well under the sequential sum.
    assert elapsed < delay * 5 * 0.6


@pytest.mark.asyncio
async def test_executor_preserves_repetition_order_under_concurrency() -> None:
    import asyncio

    # Reverse the natural completion order: rep 0 sleeps longest so, if order
    # were determined by completion, it would land last. gather must restore it.
    counter = {"n": 0}

    async def staggered(req: AdapterRequest) -> AdapterResponse:
        idx = counter["n"]
        counter["n"] += 1
        await asyncio.sleep(0.05 * (5 - idx))
        return AdapterResponse(content=f"rep-{idx}", stop_reason="end_turn")

    executor = Executor(
        adapter=EmbeddedAdapter(staggered, agent=_agent()),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
        concurrency=8,
    )
    run = await executor.run_case(_case(), repetitions=5)
    assert [r.repetition for r in run.repetitions] == [0, 1, 2, 3, 4]


@pytest.mark.asyncio
async def test_executor_one_rep_errors_others_succeed() -> None:
    import asyncio

    from selfevals.runner.adapters import AdapterError

    counter = {"n": 0}

    async def flaky(req: AdapterRequest) -> AdapterResponse:
        idx = counter["n"]
        counter["n"] += 1
        await asyncio.sleep(0.01)
        if idx == 1:
            raise AdapterError("rep 1 boom")
        return AdapterResponse(content="pong", stop_reason="end_turn")

    executor = Executor(
        adapter=EmbeddedAdapter(flaky, agent=_agent()),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    run = await executor.run_case(_case(), repetitions=4)
    assert run.successful_count == 3
    assert run.failed_count == 1
    errored = [r for r in run.repetitions if r.error is not None]
    assert len(errored) == 1
    assert "boom" in errored[0].error  # type: ignore[operator]


def _llm_span(rep: Any) -> LLMCallSpan:
    spans = [s for s in rep.trace.spans if isinstance(s, LLMCallSpan)]
    assert len(spans) == 1
    return spans[0]


def _router(tmp_path: Any) -> Any:
    from selfevals.storage.filesystem import FilesystemObjectStore
    from selfevals.trace.payload_router import PayloadRouter

    store = FilesystemObjectStore(tmp_path / "objects")
    return PayloadRouter(store, workspace_id=WS), store


@pytest.mark.asyncio
async def test_executor_inlines_small_prompt_and_response(tmp_path: Any) -> None:
    """The whole 'lonche': a small prompt/response is inlined on the LLM span so
    the trace viewer shows it without resolving a pointer."""
    router, _store = _router(tmp_path)
    executor = Executor(
        adapter=EmbeddedAdapter(_ping, agent=_agent()),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
        payload_router=router,
    )
    rep = (await executor.run_case(_case())).repetitions[0]
    span = _llm_span(rep)
    # Response text inlined, no pointer needed (under the inline cap).
    assert span.output.content_inline == "pong"
    assert span.output.content_pointer is None
    # Input messages inlined too.
    assert span.messages_inline is not None
    assert "hi" in span.messages_inline
    # Model is real, not "unknown" (the agent record carries it).
    assert span.model == "claude-sonnet-4-6"
    assert span.provider == "anthropic"


@pytest.mark.asyncio
async def test_executor_offloads_large_response_to_pointer(tmp_path: Any) -> None:
    """A response larger than the inline cap is offloaded to the object store and
    referenced by a resolvable pointer."""
    router, store = _router(tmp_path)
    big = "x" * 9000

    def _big(req: AdapterRequest) -> AdapterResponse:
        return AdapterResponse(content=big, stop_reason="end_turn")

    executor = Executor(
        adapter=EmbeddedAdapter(_big, agent=_agent()),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
        payload_router=router,
    )
    rep = (await executor.run_case(_case(expected=Expected()))).repetitions[0]
    span = _llm_span(rep)
    assert span.output.content_pointer is not None
    # The pointer resolves to the full content in the object store.
    resolved = store.get(span.output.content_pointer).decode("utf-8")
    assert resolved == big
    # A truncated inline preview is still present for a cheap render.
    assert span.output.content_inline is not None
    assert len(span.output.content_inline) <= 4096


@pytest.mark.asyncio
async def test_executor_model_from_provider_metadata(tmp_path: Any) -> None:
    """An embedded agent declares no model in its spec; the model it reports via
    `provider_metadata` is what lands on the span (not 'unknown')."""

    def _reports_model(req: AdapterRequest) -> AdapterResponse:
        return AdapterResponse(
            content="pong",
            stop_reason="end_turn",
            provider_metadata={"provider": "openai", "model": "gpt-5"},
        )

    executor = Executor(
        adapter=EmbeddedAdapter(_reports_model),  # no agent attached
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    rep = (await executor.run_case(_case())).repetitions[0]
    span = _llm_span(rep)
    assert span.model == "gpt-5"
    assert span.provider == "openai"


@pytest.mark.asyncio
async def test_executor_surfaces_structured_output(tmp_path: Any) -> None:
    """A structured response is surfaced on the trace outputs so the FE can show
    the detected structured payload."""

    def _structured(req: AdapterRequest) -> AdapterResponse:
        return AdapterResponse(
            content=None,
            structured_output={"intent": "buy", "sku": "ABC"},
            stop_reason="end_turn",
        )

    executor = Executor(
        adapter=EmbeddedAdapter(_structured, agent=_agent()),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    rep = (await executor.run_case(_case(expected=Expected()))).repetitions[0]
    assert rep.trace.outputs.structured_output == {"intent": "buy", "sku": "ABC"}


@pytest.mark.asyncio
async def test_executor_derives_cost_from_declared_model(tmp_path: Any) -> None:
    """A cli/http agent that returns tokens but no cost_usd gets priced from its
    spec-declared `agent.model` — the fix for cost_usd always 0 (Gap 6)."""

    def _tokens_no_cost(req: AdapterRequest) -> AdapterResponse:
        return AdapterResponse(
            content="ok",
            tokens_input=1000,
            tokens_output=500,
            cost_usd=0.0,  # adapter reports no cost
            stop_reason="end_turn",
        )

    adapter = EmbeddedAdapter(_tokens_no_cost)  # no Agent record (like http)
    adapter.model = ModelRef(provider="anthropic", name="claude-sonnet-4-6")
    executor = Executor(
        adapter=adapter,
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    rep = (await executor.run_case(_case())).repetitions[0]
    span = _llm_span(rep)
    # 1000 in @ $3/Mtok + 500 out @ $15/Mtok = 0.003 + 0.0075 = 0.0105
    assert span.cost_usd.total == pytest.approx(0.0105)
    assert span.model == "claude-sonnet-4-6"
    assert span.provider == "anthropic"
    # Aggregated onto the trace metrics too.
    assert rep.trace.metrics.total_cost_usd == pytest.approx(0.0105)
    assert rep.trace.metrics.llm_call_count == 1


def test_span_view_exposes_provider_metadata_and_accounting() -> None:
    """The span detail surfaces the full accounting the FE wants to show:
    tokens, cost, reasoning, provider_metadata, model."""
    from selfevals.trace.span_view import span_view

    rec = TraceRecorder(
        workspace_id=WS,
        run=RunInfo(run_id="run_x"),
        agent=AgentSnapshotRef(agent_id="ag_x", agent_version=1),
        framework_version="t",
        runtime="python-3.12",
        sandbox=SandboxMode.MOCK,
    )
    with rec, rec.agent_turn("t"), rec.llm_call("c", provider="openai", model="gpt-5") as llm:
        llm.add_tokens(input=10, output=5, reasoning=3)
        llm.provider_metadata = {"system_fingerprint": "fp_abc", "finish_reason": "stop"}
    trace = rec.build()
    llm_span = next(s for s in trace.spans if isinstance(s, LLMCallSpan))
    detail = span_view(llm_span)["detail"]
    assert detail["model"] == "gpt-5"
    assert detail["provider"] == "openai"
    assert detail["tokens"]["input"] == 10
    assert detail["tokens"]["reasoning"] == 3
    assert detail["provider_metadata"]["system_fingerprint"] == "fp_abc"


# --- OTLP wiring: out-of-process agents export their own spans into the case
# trace via the receiver bound around adapter.invoke() (the seals use case).


def _otlp_payload_with_llm_span() -> bytes:
    """A minimal OTLP/protobuf body carrying one OpenInference LLM span —
    what an instrumented agent's exporter would POST during invoke()."""
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )
    from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
    from opentelemetry.proto.resource.v1.resource_pb2 import Resource
    from opentelemetry.proto.trace.v1.trace_pb2 import ResourceSpans, ScopeSpans, Span

    req = ExportTraceServiceRequest()
    rs = ResourceSpans()
    rs.resource.CopyFrom(Resource())
    ss = ScopeSpans()
    proto = Span()
    proto.name = "ChatOpenAI"
    proto.span_id = b"\x01" * 8
    proto.trace_id = b"\x02" * 16
    proto.start_time_unix_nano = 1_000_000_000
    proto.end_time_unix_nano = 3_400_000_000
    for k, v in {"openinference.span.kind": "LLM", "llm.model_name": "gpt-5.4-mini"}.items():
        kv = KeyValue()
        kv.key = k
        kv.value.CopyFrom(AnyValue(string_value=v))
        proto.attributes.append(kv)
    ss.spans.append(proto)
    rs.scope_spans.append(ss)
    req.resource_spans.append(rs)
    return req.SerializeToString()


@pytest.mark.asyncio
async def test_executor_nests_agent_otlp_spans_under_case() -> None:
    """When an OTLP receiver is bound, spans an out-of-process agent exports to
    `request.otlp_endpoint` during invoke() land in this case's trace — not just
    the synthetic `adapter_response`. This is the seals path: the agent runs the
    real graph (ChatOpenAI) and ships its spans to selfevals instead of (only)
    LangSmith."""
    import urllib.request

    from selfevals.runner.otlp_receiver import start_receiver

    posted_endpoint: dict[str, str | None] = {}

    def _agent_that_exports(req: AdapterRequest) -> AdapterResponse:
        # Mirror what seals' /invoke does: read the endpoint we were handed and
        # POST our own spans there before returning.
        posted_endpoint["url"] = req.otlp_endpoint
        if req.otlp_endpoint:
            http_req = urllib.request.Request(
                req.otlp_endpoint + "/v1/traces",
                data=_otlp_payload_with_llm_span(),
                headers={"Content-Type": "application/x-protobuf"},
                method="POST",
            )
            with urllib.request.urlopen(http_req) as resp:
                assert resp.status == 200
        return AdapterResponse(content="Business - Sales", stop_reason="end_turn")

    with start_receiver() as handle:
        executor = Executor(
            adapter=EmbeddedAdapter(_agent_that_exports, agent=_agent()),
            sandbox=SandboxPolicy(SandboxMode.MOCK),
            workspace_id=WS,
            otlp_handle=handle,
        )
        rep = (await executor.run_case(_case())).repetitions[0]

    # The agent received the receiver's endpoint via the request.
    assert posted_endpoint["url"] == handle.endpoint
    # Its exported LLM span got drained into this case's trace (on top of the
    # synthetic adapter_response span the executor always records).
    llm_spans = [s for s in rep.trace.spans if isinstance(s, LLMCallSpan)]
    names = {s.name for s in llm_spans}
    assert "ChatOpenAI" in names
    assert "adapter_response" in names


@pytest.mark.asyncio
async def test_executor_without_receiver_omits_otlp_endpoint() -> None:
    """No receiver (embedded path / legacy) → request.otlp_endpoint is None and
    the trace has only the synthetic span. Nothing regresses."""
    seen: dict[str, str | None] = {}

    def _peek(req: AdapterRequest) -> AdapterResponse:
        seen["url"] = req.otlp_endpoint
        return AdapterResponse(content="pong", stop_reason="end_turn")

    executor = Executor(
        adapter=EmbeddedAdapter(_peek, agent=_agent()),
        sandbox=SandboxPolicy(SandboxMode.MOCK),
        workspace_id=WS,
    )
    rep = (await executor.run_case(_case())).repetitions[0]
    assert seen["url"] is None
    assert {s.name for s in rep.trace.spans if isinstance(s, LLMCallSpan)} == {"adapter_response"}
