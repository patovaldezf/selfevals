"""Runner: connect EvalCase + Agent + Sandbox → Trace.

Components:
- AgentAdapter (ABC) and concrete EmbeddedAdapter / CliCommandAdapter /
  HttpEndpointAdapter that invoke an agent in 3 different ways.
- AdapterRequest / AdapterResponse: lightweight contract between
  bootstrap and the agent runtime under test.
- SandboxPolicy: maps SandboxMode → tool-mock rules.
- Executor: orchestrates running a case across N repetitions and produces
  CaseRun results (a Trace per repetition + aggregated outcome).
"""

from bootstrap.runner.adapters import (
    AdapterError,
    AdapterRequest,
    AdapterResponse,
    AdapterToolUse,
    AgentAdapter,
    CliCommandAdapter,
    EmbeddedAdapter,
    EmbeddedCallable,
    HttpEndpointAdapter,
)
from bootstrap.runner.executor import CaseRun, Executor, RepetitionResult
from bootstrap.runner.sandbox import SandboxPolicy, SandboxViolationError

__all__ = [
    "AdapterError",
    "AdapterRequest",
    "AdapterResponse",
    "AdapterToolUse",
    "AgentAdapter",
    "CaseRun",
    "CliCommandAdapter",
    "EmbeddedAdapter",
    "EmbeddedCallable",
    "Executor",
    "HttpEndpointAdapter",
    "RepetitionResult",
    "SandboxPolicy",
    "SandboxViolationError",
]
