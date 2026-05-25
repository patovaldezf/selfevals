"""Name → factory mapping for graders referenced by string in YAML.

Cases can declare which graders apply via `EvalCase.graders: list[str]`.
When the CLI builds the OptimizationLoop it materialises the union of
all referenced names through this registry. Anything that is not
registered raises a :class:`BootstrapUserError` with a list of the
available names — no silent fallthrough.

For MVP the registry is module-level; integration tests reset it in a
fixture. Post-MVP we can scope it per Experiment if cross-experiment
isolation becomes important.
"""

from __future__ import annotations

from collections.abc import Callable

from bootstrap.graders.base import Grader
from bootstrap.graders.deterministic import DeterministicGrader

GraderFactory = Callable[[], Grader]

_REGISTRY: dict[str, GraderFactory] = {}


def register_grader(name: str, factory: GraderFactory) -> None:
    """Register a grader factory under `name`. Last write wins.

    Idempotent for the common case of a module being imported twice.
    """
    if not name:
        raise ValueError("grader name must be non-empty")
    _REGISTRY[name] = factory


def unregister_grader(name: str) -> None:
    """Remove a grader from the registry (test-only helper)."""
    _REGISTRY.pop(name, None)


def available_graders() -> list[str]:
    return sorted(_REGISTRY)


def resolve_graders(names: list[str]) -> list[Grader]:
    """Materialise a list of grader instances from registered names.

    Raises :class:`BootstrapUserError` with the list of available names
    when any entry is missing — this is the path that produces the
    "Grader 'foo' not registered" message in the troubleshooting doc.
    """
    from bootstrap.cli._friendly import unknown_grader  # avoid CLI ↔ graders import cycle

    out: list[Grader] = []
    for n in names:
        factory = _REGISTRY.get(n)
        if factory is None:
            raise unknown_grader(n, list(_REGISTRY))
        out.append(factory())
    return out


# Built-in factories. Registered eagerly on import so that anything that
# imports `bootstrap.graders` (or just this module) sees the defaults.

register_grader("deterministic", DeterministicGrader)
