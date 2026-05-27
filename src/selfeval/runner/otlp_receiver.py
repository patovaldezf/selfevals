"""Embedded OTLP/HTTP receiver hosted inside `selfeval run`.

Listens on a free localhost port for `POST /v1/traces` requests carrying
OpenTelemetry protobuf bodies. Decoded spans are routed to whichever
`TraceRecorder` is currently bound (set by the Executor before each
repetition, unset after). Spans that arrive while nothing is bound get
buffered so a late-arriving SDK exporter still lands.

Threading model:
- HTTP server runs in a background daemon thread.
- Span ingestion appends to a `deque` guarded by a `threading.Lock`.
- The main thread (Executor) calls `with handle.bind_recorder(recorder):`
  around each agent invocation. `bind_recorder.__exit__` drains pending
  spans into the recorder before returning.

Optional publish hook: when running under `selfeval serve`, the
embedded broker is installed via `set_publisher()` and every routed
span is fan-out'd to live SSE subscribers. `selfeval run` (CLI-only)
leaves the hook unset and pays nothing.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from selfeval.runner.otlp_to_recorder import (
    DecodedSpan,
    decode_otlp_protobuf,
    feed_to_recorder,
)


class SpanPublisher:
    """Pluggable fan-out hook installed via `ReceiverHandle.set_publisher`.

    Default implementation is a no-op so callers can ignore it.
    `selfeval serve` installs a concrete one backed by `SpanBroker`."""

    def mark_active(
        self, workspace_id: str, run_id: str
    ) -> None:  # pragma: no cover - protocol-like default
        return None

    def publish(
        self, workspace_id: str, run_id: str, span_payload: dict[str, Any]
    ) -> None:  # pragma: no cover
        return None

    def close(
        self, workspace_id: str, run_id: str, final_state: str = "completed"
    ) -> None:  # pragma: no cover
        return None


logger = logging.getLogger(__name__)


@dataclass
class ReceiverStats:
    requests_received: int = 0
    spans_received: int = 0
    spans_routed: int = 0
    decode_errors: int = 0


class ReceiverHandle:
    """Public handle returned from `start_receiver()`."""

    def __init__(
        self,
        *,
        server: ThreadingHTTPServer,
        thread: threading.Thread,
        endpoint: str,
        ingest: _SpanIngest,
        stats: ReceiverStats,
        flush_timeout_seconds: float,
    ) -> None:
        self._server = server
        self._thread = thread
        self.endpoint = endpoint
        self._ingest = ingest
        self.stats = stats
        self._flush_timeout = flush_timeout_seconds
        self._stopped = False

    def set_publisher(self, publisher: SpanPublisher | None) -> None:
        """Install (or clear) a span fan-out hook called for every
        routed span. Used by `selfeval serve` to wire SSE; CLI-only
        runs leave it None."""
        self._ingest.set_publisher(publisher)

    @contextmanager
    def bind_recorder(self, recorder: Any) -> Iterator[None]:
        """Route spans into `recorder` for the duration of the block.

        On exit: any spans still in the queue are drained into the
        recorder before unbinding so we don't lose late arrivals from
        the SDK's BatchSpanProcessor. The broker (if installed) is
        notified that the run is complete so SSE subscribers get a
        close event.
        """
        self._ingest.bind(recorder)
        publish_keys = self._ingest.publish_keys_unlocked()
        if publish_keys is not None:
            self._ingest.notify_active(*publish_keys)
        try:
            yield
        finally:
            self._ingest.drain_into_current()
            self._ingest.unbind()
            if publish_keys is not None:
                self._ingest.notify_complete(*publish_keys)

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        try:
            self._server.shutdown()
        except Exception as exc:
            logger.warning("OTLP receiver shutdown error: %s", exc)
        try:
            self._server.server_close()
        except Exception as exc:
            logger.warning("OTLP receiver close error: %s", exc)
        self._thread.join(timeout=self._flush_timeout)

    def __enter__(self) -> ReceiverHandle:
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()


class _SpanIngest:
    """Thread-safe span queue with a 'current recorder' slot."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: deque[DecodedSpan] = deque()
        self._recorder: Any = None
        self._stats = ReceiverStats()
        self._publisher: SpanPublisher | None = None
        self._publish_keys: tuple[str, str] | None = None

    @property
    def stats(self) -> ReceiverStats:
        return self._stats

    def set_publisher(self, publisher: SpanPublisher | None) -> None:
        with self._lock:
            self._publisher = publisher

    def publish_keys_unlocked(self) -> tuple[str, str] | None:
        # Safe read without the lock: bind/unbind run on the main
        # thread and bind_recorder calls this immediately after bind().
        return self._publish_keys

    def notify_active(self, workspace_id: str, run_id: str) -> None:
        with self._lock:
            publisher = self._publisher
        if publisher is None:
            return
        try:
            publisher.mark_active(workspace_id, run_id)
        except Exception as exc:
            logger.warning("span publisher mark_active raised: %s", exc)

    def notify_complete(self, workspace_id: str, run_id: str) -> None:
        with self._lock:
            publisher = self._publisher
        if publisher is None:
            return
        try:
            publisher.close(workspace_id, run_id)
        except Exception as exc:
            logger.warning("span publisher close raised: %s", exc)

    def bind(self, recorder: Any) -> None:
        # Capture (workspace_id, run_id) for the lifetime of this
        # binding so publish() doesn't have to peek into recorder
        # internals on every span.
        keys: tuple[str, str] | None = None
        try:
            ws = getattr(recorder, "workspace_id", None) or getattr(recorder, "_workspace_id", None)
            run = getattr(recorder, "_run", None)
            run_id = getattr(run, "run_id", None) if run is not None else None
            if ws and run_id:
                keys = (str(ws), str(run_id))
        except Exception as exc:
            logger.debug("ingest.bind: cannot extract publish keys: %s", exc)
        with self._lock:
            self._recorder = recorder
            self._publish_keys = keys

    def unbind(self) -> None:
        with self._lock:
            self._recorder = None
            self._publish_keys = None

    def push(self, spans: list[DecodedSpan]) -> None:
        if not spans:
            return
        with self._lock:
            self._stats.spans_received += len(spans)
            if self._recorder is not None:
                routed = feed_to_recorder(spans, self._recorder)
                self._stats.spans_routed += routed
                self._fan_out_locked(spans)
            else:
                self._pending.extend(spans)

    def drain_into_current(self) -> int:
        with self._lock:
            if self._recorder is None or not self._pending:
                return 0
            to_send = list(self._pending)
            self._pending.clear()
            routed = feed_to_recorder(to_send, self._recorder)
            self._stats.spans_routed += routed
            self._fan_out_locked(to_send)
            return routed

    def peek_pending(self) -> int:
        with self._lock:
            return len(self._pending)

    def _fan_out_locked(self, spans: list[DecodedSpan]) -> None:
        """Publish routed spans to the broker, if one is installed.

        Called with `self._lock` held; the publisher must NOT block on
        the lock (broker.publish_threadsafe schedules onto an event
        loop and returns immediately — that's the contract)."""
        publisher = self._publisher
        keys = self._publish_keys
        if publisher is None or keys is None:
            return
        ws, run = keys
        for span in spans:
            try:
                publisher.publish(ws, run, dict(span.payload))
            except Exception as exc:
                logger.warning("span publisher raised: %s", exc)


def _make_handler(ingest: _SpanIngest, stats: ReceiverStats) -> type[BaseHTTPRequestHandler]:
    class _Handler(BaseHTTPRequestHandler):
        # Don't keep TCP connections alive across requests; tests
        # routinely tear down the server right after one request, and
        # half-open keep-alive sockets trigger ResourceWarnings.
        protocol_version = "HTTP/1.0"

        # Quiet down the default per-request stderr noise.
        def log_message(self, format: str, *args: Any) -> None:
            logger.debug("otlp_receiver: " + format, *args)

        def do_POST(self) -> None:
            stats.requests_received += 1
            if self.path.rstrip("/") not in {"/v1/traces", ""}:
                self.send_error(404, "Only /v1/traces is supported")
                return
            length = int(self.headers.get("Content-Length", "0") or 0)
            body = self.rfile.read(length) if length else b""
            try:
                spans = decode_otlp_protobuf(body)
            except Exception as exc:
                stats.decode_errors += 1
                logger.warning("OTLP decode error: %s", exc)
                self.send_error(400, f"decode error: {exc}")
                return
            ingest.push(spans)
            self.send_response(200)
            self.send_header("Content-Type", "application/x-protobuf")
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()

        def do_GET(self) -> None:
            # Cheap liveness probe.
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(b"selfeval-otlp-receiver")

    return _Handler


class _CleanShutdownHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer that waits for in-flight requests on close.

    Default `daemon_threads=True` means request workers can outlive the
    server, leaving their client sockets open and triggering
    `ResourceWarning` under strict warning filters. Setting
    `block_on_close=True` and `daemon_threads=False` makes server_close()
    join the per-request threads first.
    """

    daemon_threads = False
    block_on_close = True


def start_receiver(
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    flush_timeout_seconds: float = 5.0,
) -> ReceiverHandle:
    """Bind a localhost OTLP/HTTP receiver and return a handle.

    `port=0` picks a free OS-assigned port. The returned `endpoint` is
    the base URL (no path), e.g. `http://127.0.0.1:54321`. Exporters
    should POST to `{endpoint}/v1/traces`.
    """
    ingest = _SpanIngest()
    stats = ingest.stats
    handler_cls = _make_handler(ingest, stats)
    server = _CleanShutdownHTTPServer((host, port), handler_cls)
    bound_port = server.server_address[1]
    endpoint = f"http://{host}:{bound_port}"
    thread = threading.Thread(
        target=server.serve_forever,
        name="selfeval-otlp-receiver",
        daemon=True,
    )
    thread.start()
    logger.info("OTLP receiver listening at %s/v1/traces", endpoint)
    return ReceiverHandle(
        server=server,
        thread=thread,
        endpoint=endpoint,
        ingest=ingest,
        stats=stats,
        flush_timeout_seconds=flush_timeout_seconds,
    )
