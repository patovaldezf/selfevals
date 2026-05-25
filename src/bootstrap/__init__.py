"""bootstrap — self-improving evals framework for AI agents."""

from bootstrap.sdk import (
    BootstrapAlreadyInitialized,
    InitResult,
    init,
    is_initialized,
    shutdown,
)
from bootstrap.version import __version__

__all__ = [
    "BootstrapAlreadyInitialized",
    "InitResult",
    "__version__",
    "init",
    "is_initialized",
    "shutdown",
]
