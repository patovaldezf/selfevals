"""selfevals — self-improving evals framework for AI agents."""

from selfevals.graders.base import (
    GradeLabel,
    Grader,
    GraderContext,
    GradeResult,
)
from selfevals.runner.adapters import (
    AdapterRequest,
    AdapterResponse,
    AdapterToolUse,
    AgentAdapter,
)
from selfevals.sdk import (
    InitResult,
    SelfEvalsAlreadyInitialized,
    init,
    is_initialized,
    shutdown,
)
from selfevals.version import __version__

__all__ = [
    "AdapterRequest",
    "AdapterResponse",
    "AdapterToolUse",
    "AgentAdapter",
    "GradeLabel",
    "GradeResult",
    "Grader",
    "GraderContext",
    "InitResult",
    "SelfEvalsAlreadyInitialized",
    "__version__",
    "init",
    "is_initialized",
    "shutdown",
]
