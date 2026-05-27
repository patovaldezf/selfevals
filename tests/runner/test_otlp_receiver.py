"""Integration tests for the embedded OTLP/HTTP receiver."""

from __future__ import annotations

import gc
import urllib.request
from urllib.parse import urlparse

import pytest

from selfevals.runner.otlp_receiver import start_receiver


@pytest.fixture(autouse=True)
def _force_gc() -> None:
    """Force a GC cycle after each test so leftover urllib sockets are
    finalized inside the test rather than triggering a delayed
    ResourceWarning on a later test."""
    yield
    gc.collect()


def _make_otlp_protobuf_request(spans: list[dict]) -> bytes:
    """Build a minimal ExportTraceServiceRequest protobuf payload."""
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )
    from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
    from opentelemetry.proto.resource.v1.resource_pb2 import Resource
    from opentelemetry.proto.trace.v1.trace_pb2 import (
        ResourceSpans,
        ScopeSpans,
        Span,
    )

    request = ExportTraceServiceRequest()
    rs = ResourceSpans()
    rs.resource.CopyFrom(Resource())
    ss = ScopeSpans()
    for s in spans:
        proto = Span()
        proto.name = s["name"]
        proto.span_id = b"\x01" * 8
        proto.trace_id = b"\x02" * 16
        proto.start_time_unix_nano = s.get("start_ns", 1_000_000_000)
        proto.end_time_unix_nano = s.get("end_ns", 2_000_000_000)
        for k, v in s.get("attributes", {}).items():
            kv = KeyValue()
            kv.key = k
            if isinstance(v, str):
                kv.value.CopyFrom(AnyValue(string_value=v))
            elif isinstance(v, int):
                kv.value.CopyFrom(AnyValue(int_value=v))
            elif isinstance(v, float):
                kv.value.CopyFrom(AnyValue(double_value=v))
            proto.attributes.append(kv)
        ss.spans.append(proto)
    rs.scope_spans.append(ss)
    request.resource_spans.append(rs)
    return request.SerializeToString()


def test_receiver_starts_on_free_port() -> None:
    with start_receiver() as handle:
        parsed = urlparse(handle.endpoint)
        assert parsed.scheme == "http"
        assert parsed.hostname == "127.0.0.1"
        assert parsed.port and parsed.port > 0


def test_receiver_get_liveness() -> None:
    with start_receiver() as handle:
        with urllib.request.urlopen(handle.endpoint + "/") as resp:
            assert resp.status == 200
            body = resp.read()
        assert b"selfevals-otlp-receiver" in body


def test_receiver_404_on_wrong_path() -> None:
    with start_receiver() as handle:
        req = urllib.request.Request(
            handle.endpoint + "/wrong",
            data=b"",
            method="POST",
        )
        try:
            urllib.request.urlopen(req)
            pytest.fail("expected HTTPError 404")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
            # HTTPError holds the response fp open until we read+close it.
            exc.read()
            exc.close()


def test_receiver_buffers_spans_when_no_recorder_bound() -> None:
    body = _make_otlp_protobuf_request(
        [{"name": "llm.call", "attributes": {"gen_ai.system": "anthropic"}}]
    )
    with start_receiver() as handle:
        req = urllib.request.Request(
            handle.endpoint + "/v1/traces",
            data=body,
            method="POST",
            headers={"Content-Type": "application/x-protobuf"},
        )
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
        assert handle.stats.spans_received == 1
        # No recorder bound — span went to pending buffer.
        assert handle.stats.spans_routed == 0


def test_receiver_routes_spans_to_bound_recorder() -> None:
    from selfevals._internal.time import utc_now
    from selfevals.schemas.enums import SandboxMode
    from selfevals.schemas.trace import AgentSnapshotRef, RunInfo
    from selfevals.trace.recorder import TraceRecorder

    rec = TraceRecorder(
        workspace_id="ws_test",
        run=RunInfo(run_id="run_test"),
        agent=AgentSnapshotRef(agent_id="ag", agent_version=1),
        framework_version="test",
        runtime="py",
        sandbox=SandboxMode.MOCK,
        environment_started_at=utc_now(),
    )

    body = _make_otlp_protobuf_request(
        [
            {
                "name": "anthropic.messages.create",
                "attributes": {
                    "gen_ai.system": "anthropic",
                    "gen_ai.request.model": "claude-sonnet-4-6",
                    "gen_ai.usage.input_tokens": 100,
                    "gen_ai.usage.output_tokens": 20,
                },
            }
        ]
    )

    with start_receiver() as handle:
        with handle.bind_recorder(rec):
            req = urllib.request.Request(
                handle.endpoint + "/v1/traces",
                data=body,
                method="POST",
            )
            with urllib.request.urlopen(req) as r:
                r.read()
        assert handle.stats.spans_routed == 1

    # Recorder should now have one LLM span.
    trace = rec.build()
    assert any(s.name == "anthropic.messages.create" for s in trace.spans)
    llm = next(s for s in trace.spans if s.name == "anthropic.messages.create")
    assert llm.provider == "anthropic"
    assert llm.model == "claude-sonnet-4-6"
    assert llm.tokens.input == 100
    assert llm.tokens.output == 20


def test_receiver_drains_pending_on_bind_exit() -> None:
    """Spans buffered before bind_recorder should land in the recorder."""
    from selfevals._internal.time import utc_now
    from selfevals.schemas.enums import SandboxMode
    from selfevals.schemas.trace import AgentSnapshotRef, RunInfo
    from selfevals.trace.recorder import TraceRecorder

    rec = TraceRecorder(
        workspace_id="ws_test",
        run=RunInfo(run_id="run_test"),
        agent=AgentSnapshotRef(agent_id="ag", agent_version=1),
        framework_version="test",
        runtime="py",
        sandbox=SandboxMode.MOCK,
        environment_started_at=utc_now(),
    )
    body = _make_otlp_protobuf_request(
        [{"name": "early.span", "attributes": {"gen_ai.system": "anthropic"}}]
    )

    with start_receiver() as handle:
        # Send BEFORE binding — span goes to pending.
        req = urllib.request.Request(handle.endpoint + "/v1/traces", data=body, method="POST")
        with urllib.request.urlopen(req) as r:
            r.read()
        assert handle.stats.spans_routed == 0

        # Now bind and let __exit__ drain the buffer.
        with handle.bind_recorder(rec):
            pass

        assert handle.stats.spans_routed == 1
