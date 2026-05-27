"""selfevals — self-improving evals framework for AI agents."""

from selfevals.sdk import (
    InitResult,
    SelfEvalsAlreadyInitialized,
    init,
    is_initialized,
    shutdown,
)
from selfevals.version import __version__

__all__ = [
    "InitResult",
    "SelfEvalsAlreadyInitialized",
    "__version__",
    "init",
    "is_initialized",
    "shutdown",
]
