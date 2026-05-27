"""selfeval — self-improving evals framework for AI agents."""

from selfeval.sdk import (
    InitResult,
    SelfEvalAlreadyInitialized,
    init,
    is_initialized,
    shutdown,
)
from selfeval.version import __version__

__all__ = [
    "InitResult",
    "SelfEvalAlreadyInitialized",
    "__version__",
    "init",
    "is_initialized",
    "shutdown",
]
