"""selfeval.init() — the one-line user-side activation.

Resolves an OTLP endpoint, builds an OpenTelemetry TracerProvider with a
BatchSpanProcessor pointed at it, then walks the known OpenInference
Instrumentors and activates the ones that match the user's installed SDKs.

Designed so that:
- Calling it without telemetry extras is non-fatal (no-op exporter, warning).
- Calling it twice with the same project is idempotent.
- Calling it twice with different projects raises — that almost always
  means a misconfigured user app and silent re-init would be confusing.
"""

from __future__ import annotations

import contextlib
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from selfeval.sdk import context as ctx
from selfeval.sdk.auto_instrument import InstallReport, install_instrumentors
from selfeval.sdk.exporter import build_exporter

logger = logging.getLogger("selfeval.sdk")


class SelfEvalAlreadyInitialized(RuntimeError):  # noqa: N818 — public API name
    """Raised when init() is called twice with a different project."""


@dataclass
class InitResult:
    project: str
    endpoint: str | None
    instrumentors_installed: list[str] = field(default_factory=list)
    instrumentors_skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    tracer_provider: Any = None
    # True when we set the global TracerProvider; False when we kept the
    # caller's existing one (to avoid double-instrumentation conflicts).
    owns_tracer_provider: bool = True


_state: InitResult | None = None
_span_processor: Any = None


def is_initialized() -> bool:
    return _state is not None


def init(
    *,
    project: str,
    endpoint: str | None = None,
    sample_rate: float = 1.0,
    instrument: list[str] | None = None,
    disable: list[str] | None = None,
    propagate_to_parent: bool = True,
) -> InitResult:
    """Activate selfeval telemetry. See module docstring for behaviour."""
    global _state, _span_processor

    if not project or not isinstance(project, str):
        raise ValueError("project must be a non-empty string")
    if not 0.0 <= sample_rate <= 1.0:
        raise ValueError("sample_rate must be in [0.0, 1.0]")

    if _state is not None:
        if _state.project != project:
            raise SelfEvalAlreadyInitialized(
                f"selfeval.init() already called with project={_state.project!r}; "
                f"refusing to re-init with project={project!r}"
            )
        return _state

    resolved_endpoint = _resolve_endpoint(endpoint)
    ctx.set_tags(ctx.tags_from_env(project_fallback=project))

    # Try the heavy OTel SDK imports. If the user installed selfeval
    # without [telemetry], we degrade to a result that records the warning
    # but doesn't crash their app.
    try:
        from opentelemetry import trace as ot_trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
    except ImportError:
        result = InitResult(
            project=project,
            endpoint=None,
            warnings=[
                "OpenTelemetry SDK not installed; selfeval.init() is a no-op. "
                "Install with `pip install 'selfeval[telemetry]'`."
            ],
            tracer_provider=None,
            owns_tracer_provider=False,
        )
        _state = result
        return result

    current = ctx.current_tags()
    resource = Resource.create(
        {
            "service.name": project,
            "selfeval.project": project,
            "selfeval.iteration_id": (current.iteration_id if current else "") or "",
            "selfeval.run_id": (current.run_id if current else "") or "",
        }
    )
    sampler = TraceIdRatioBased(sample_rate) if sample_rate < 1.0 else None

    existing_provider = ot_trace.get_tracer_provider()
    owns_provider = True
    if propagate_to_parent and _is_real_tracer_provider(existing_provider):
        # User (or another lib) already set up tracing. Reuse it so we
        # don't end up with two providers fighting over the global slot.
        provider = existing_provider
        owns_provider = False
        warnings = [
            "An existing TracerProvider was detected; selfeval is attaching "
            "its exporter to it instead of replacing it."
        ]
        # Still attach our exporter to the existing provider when it
        # supports it (the SDK's TracerProvider does).
        exporter = build_exporter(resolved_endpoint)
        if hasattr(provider, "add_span_processor"):
            _span_processor = BatchSpanProcessor(exporter)
            provider.add_span_processor(_span_processor)
    else:
        provider = (
            TracerProvider(resource=resource, sampler=sampler)
            if sampler
            else TracerProvider(resource=resource)
        )
        exporter = build_exporter(resolved_endpoint)
        _span_processor = BatchSpanProcessor(exporter)
        provider.add_span_processor(_span_processor)
        ot_trace.set_tracer_provider(provider)
        warnings = []

    report: InstallReport = install_instrumentors(
        tracer_provider=provider,
        requested=instrument,
        disabled=disable,
    )

    if resolved_endpoint is None:
        warnings.append(
            "No OTLP endpoint configured (set SELFEVAL_OTLP_ENDPOINT or pass endpoint=). "
            "Spans will be collected but not exported."
        )
    warnings.extend(report.warnings)

    result = InitResult(
        project=project,
        endpoint=resolved_endpoint,
        instrumentors_installed=report.installed,
        instrumentors_skipped=report.skipped_missing_extra,
        warnings=warnings,
        tracer_provider=provider,
        owns_tracer_provider=owns_provider,
    )
    _state = result
    for w in warnings:
        logger.info(w)
    return result


def shutdown() -> None:
    """Flush exporters and reset module state. Safe to call repeatedly."""
    global _state, _span_processor
    if _span_processor is not None:
        with contextlib.suppress(Exception):
            _span_processor.shutdown()
        _span_processor = None
    _state = None


def _resolve_endpoint(explicit: str | None) -> str | None:
    if explicit is not None:
        return explicit
    env_endpoint = os.environ.get("SELFEVAL_OTLP_ENDPOINT")
    if env_endpoint:
        return env_endpoint
    env_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if env_endpoint:
        return env_endpoint
    return None


def _is_real_tracer_provider(provider: Any) -> bool:
    """The OTel default is a ProxyTracerProvider — treat that as 'no provider'."""
    cls_name = type(provider).__name__
    return cls_name not in {"ProxyTracerProvider", "NoOpTracerProvider"}


def _reset_for_tests() -> None:
    """Test-only hook to undo init() state between cases."""
    shutdown()
