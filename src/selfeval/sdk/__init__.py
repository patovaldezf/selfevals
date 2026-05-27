"""selfeval.sdk — user-installed surface for in-process telemetry capture.

Re-exports the one-liner facade plus a few helpers. Designed so that
`from selfeval.sdk import init` works without pulling in the OpenTelemetry
stack at import time — heavy imports happen lazily inside `init()`.
"""

from __future__ import annotations

from selfeval.sdk.facade import (
    InitResult,
    SelfEvalAlreadyInitialized,
    init,
    is_initialized,
    shutdown,
)

__all__ = [
    "InitResult",
    "SelfEvalAlreadyInitialized",
    "init",
    "is_initialized",
    "shutdown",
]
