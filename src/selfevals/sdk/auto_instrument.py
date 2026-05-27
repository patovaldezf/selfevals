"""Detect installed provider SDKs and activate the matching OpenInference
Instrumentors.

The mapping is intentionally a flat dict — each entry says: "if the user
imports `anthropic`, try to load `openinference.instrumentation.anthropic.
AnthropicInstrumentor`." Missing extras are non-fatal: we log a warning
and move on.

Detection is `importlib.util.find_spec`-based rather than `sys.modules`-
based so users who call `selfevals.init()` *before* their first
`import anthropic` still get the instrumentation wired up.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InstrumentorSpec:
    """One row of the SDK→Instrumentor table."""

    name: str
    """Short slug exposed in InitResult (e.g. 'anthropic')."""

    sdk_module: str
    """Module name we look for to decide if the user has the SDK installed."""

    instrumentor_module: str
    """Module path of the openinference Instrumentor."""

    instrumentor_class: str
    """Class name of the Instrumentor (always lives at module level)."""


# Order matters only for tie-breaking in logs.
KNOWN_INSTRUMENTORS: tuple[InstrumentorSpec, ...] = (
    InstrumentorSpec(
        name="anthropic",
        sdk_module="anthropic",
        instrumentor_module="openinference.instrumentation.anthropic",
        instrumentor_class="AnthropicInstrumentor",
    ),
    InstrumentorSpec(
        name="openai",
        sdk_module="openai",
        instrumentor_module="openinference.instrumentation.openai",
        instrumentor_class="OpenAIInstrumentor",
    ),
    InstrumentorSpec(
        name="bedrock",
        sdk_module="boto3",
        instrumentor_module="openinference.instrumentation.bedrock",
        instrumentor_class="BedrockInstrumentor",
    ),
    InstrumentorSpec(
        name="vertexai",
        sdk_module="google.cloud.aiplatform",
        instrumentor_module="openinference.instrumentation.vertexai",
        instrumentor_class="VertexAIInstrumentor",
    ),
    InstrumentorSpec(
        name="cohere",
        sdk_module="cohere",
        instrumentor_module="openinference.instrumentation.cohere",
        instrumentor_class="CohereInstrumentor",
    ),
    InstrumentorSpec(
        name="langchain",
        sdk_module="langchain",
        instrumentor_module="openinference.instrumentation.langchain",
        instrumentor_class="LangChainInstrumentor",
    ),
    # LangGraph instrumentation currently ships inside langchain. If a
    # standalone package shows up, list it under a separate spec.
    InstrumentorSpec(
        name="crewai",
        sdk_module="crewai",
        instrumentor_module="openinference.instrumentation.crewai",
        instrumentor_class="CrewAIInstrumentor",
    ),
    InstrumentorSpec(
        name="llama_index",
        sdk_module="llama_index",
        instrumentor_module="openinference.instrumentation.llama_index",
        instrumentor_class="LlamaIndexInstrumentor",
    ),
    InstrumentorSpec(
        name="dspy",
        sdk_module="dspy",
        instrumentor_module="openinference.instrumentation.dspy",
        instrumentor_class="DSPyInstrumentor",
    ),
)


def detect_installed_sdks() -> list[InstrumentorSpec]:
    """Return the subset of known specs whose SDK module is importable."""
    return [s for s in KNOWN_INSTRUMENTORS if importlib.util.find_spec(s.sdk_module) is not None]


@dataclass
class InstallReport:
    installed: list[str]
    skipped_missing_extra: list[str]
    skipped_explicit: list[str]
    warnings: list[str]


def install_instrumentors(
    *,
    tracer_provider: Any,
    requested: list[str] | None = None,
    disabled: list[str] | None = None,
) -> InstallReport:
    """Install the matching Instrumentors against `tracer_provider`.

    - `requested=None` triggers auto-detection.
    - `requested=[]` installs nothing (explicit opt-out).
    - `disabled` removes entries from whatever set we end up with.
    """
    disabled_set = {d.lower() for d in (disabled or [])}
    if requested is None:
        specs = detect_installed_sdks()
    else:
        wanted = {r.lower() for r in requested}
        specs = [s for s in KNOWN_INSTRUMENTORS if s.name in wanted]
    specs = [s for s in specs if s.name not in disabled_set]

    report = InstallReport(
        installed=[],
        skipped_missing_extra=[],
        skipped_explicit=sorted(disabled_set),
        warnings=[],
    )
    for spec in specs:
        try:
            module = importlib.import_module(spec.instrumentor_module)
            cls = getattr(module, spec.instrumentor_class)
        except (ImportError, AttributeError) as exc:
            report.skipped_missing_extra.append(spec.name)
            report.warnings.append(
                f"{spec.name}: instrumentor unavailable ({type(exc).__name__}: {exc}). "
                f"Install with `pip install 'selfevals[{spec.name}]'`."
            )
            continue
        try:
            instance = cls()
            # OpenInference Instrumentors are idempotent — calling
            # .instrument() twice on the same provider is a no-op.
            instance.instrument(tracer_provider=tracer_provider)
        except Exception as exc:
            report.skipped_missing_extra.append(spec.name)
            report.warnings.append(
                f"{spec.name}: failed to instrument ({type(exc).__name__}: {exc})."
            )
            continue
        report.installed.append(spec.name)
    return report
