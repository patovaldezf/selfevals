"""OTLP/HTTP exporter wiring.

Wraps the standard `OTLPSpanExporter` so we can attach a no-op fallback
when no endpoint is configured. Kept in its own module so tests can mock
`build_exporter` cheaply.
"""

from __future__ import annotations

from typing import Any


class _NoopExporter:
    """Fallback exporter used when no OTLP endpoint is configured."""

    def export(self, spans: object) -> int:
        # OTel SDK's SpanExportResult enum is `SUCCESS=0`. We mirror the int
        # value so we don't need to import the enum here.
        return 0

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True


def build_exporter(endpoint: str | None) -> Any:
    """Return an OTLP HTTP exporter for `endpoint` or a no-op exporter.

    Raises ImportError only if `endpoint` is set and the OTel extras are
    missing — that's a real user-actionable error. With no endpoint we
    quietly return the no-op so tests and bare installs don't pay the
    import cost.
    """
    if endpoint is None:
        return _NoopExporter()
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
    except ImportError as exc:  # pragma: no cover — environment-dependent
        raise ImportError(
            "selfeval.init(endpoint=...) requires the telemetry extras: "
            "pip install 'selfeval[telemetry]'"
        ) from exc
    # The standard OTLP HTTP exporter expects the full /v1/traces path.
    if not endpoint.rstrip("/").endswith("/v1/traces"):
        endpoint = endpoint.rstrip("/") + "/v1/traces"
    return OTLPSpanExporter(endpoint=endpoint)
