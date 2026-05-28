"""Name → factory mapping for graders referenced by string in YAML.

Cases can declare which graders apply via `EvalCase.graders: list[str]`.
When the CLI builds the OptimizationLoop it materialises the union of
all referenced names through this registry. Anything that is not
registered raises a :class:`SelfEvalsUserError` with a list of the
available names — no silent fallthrough.

The registry is module-level; integration tests reset it in a fixture.
It can be scoped per Experiment later if cross-experiment isolation
becomes important.
"""

from __future__ import annotations

from collections.abc import Callable

from selfevals.graders.artifact import ArtifactCompletenessGrader
from selfevals.graders.base import Grader
from selfevals.graders.deterministic import DeterministicGrader
from selfevals.graders.guardrail import GuardrailGrader
from selfevals.graders.trajectory import TrajectoryGrader

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

    Raises :class:`SelfEvalsUserError` with the list of available names
    when any entry is missing — this is the path that produces the
    "Grader 'foo' not registered" message in the troubleshooting doc.
    """
    from selfevals.cli._friendly import unknown_grader  # avoid CLI ↔ graders import cycle

    out: list[Grader] = []
    for n in names:
        factory = _REGISTRY.get(n)
        if factory is None:
            raise unknown_grader(n, list(_REGISTRY))
        out.append(factory())
    return out


# Built-in factories. Registered eagerly on import so that anything that
# imports `selfevals.graders` (or just this module) sees the defaults.

register_grader("artifact_completeness", ArtifactCompletenessGrader)
register_grader("deterministic", DeterministicGrader)
register_grader("guardrail", GuardrailGrader)
register_grader("trajectory", TrajectoryGrader)
