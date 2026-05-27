"""Runner: connect EvalCase + Agent + Sandbox → Trace.

Components:
- AgentAdapter (ABC) and concrete EmbeddedAdapter / CliCommandAdapter /
  HttpEndpointAdapter that invoke an agent in 3 different ways.
- AdapterRequest / AdapterResponse: lightweight contract between
  selfeval and the agent runtime under test.
- SandboxPolicy: maps SandboxMode → tool-mock rules.
- Executor: orchestrates running a case across N repetitions and produces
  CaseRun results (a Trace per repetition + aggregated outcome).
"""

from selfeval.runner.adapters import (
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
from selfeval.runner.executor import CaseRun, Executor, RepetitionResult
from selfeval.runner.sandbox import SandboxPolicy, SandboxViolationError

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
