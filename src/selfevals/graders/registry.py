"""Name → factory mapping for graders referenced by string in YAML.

Cases can declare which graders apply via `EvalCase.graders: list[str]`.
When the CLI builds the OptimizationLoop it materialises the union of
all referenced names through this registry. Anything that is not
registered raises a :class:`SelfEvalsUserError` with a list of the
available names — no silent fallthrough.

A grader name may instead be a **dotted path** of the form
``"package.module:ClassName"`` (i.e. it contains a ``:``). In that case
the class is imported on demand and instantiated, with no prior
registration needed — the declarative escape hatch for project-local
custom graders. The class must subclass :class:`Grader` and be
constructible with no required arguments. Built-in names (``deterministic``
etc.) take the registry path; dotted paths take the import path. The two
coexist.

The registry is module-level; integration tests reset it in a fixture.
It can be scoped per Experiment later if cross-experiment isolation
becomes important.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable

from selfevals.graders.artifact import ArtifactCompletenessGrader
from selfevals.graders.base import Grader
from selfevals.graders.deterministic import DeterministicGrader
from selfevals.graders.guardrail import GuardrailGrader
from selfevals.graders.set_match import SetMatchGrader
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


def _resolve_dotted_grader(spec: str) -> Grader:
    """Import and instantiate a grader from a ``"module:ClassName"`` path.

    Mirrors `repo.loader.resolve_agent_callable`: the class is imported on
    demand. Raises :class:`SelfEvalsUserError` if the module/attribute can't
    be imported, the target isn't a `Grader` subclass, or it can't be
    constructed with no arguments.
    """
    from selfevals._errors import SelfEvalsUserError

    module_name, _, attribute = spec.partition(":")
    if not module_name or not attribute:
        raise SelfEvalsUserError(
            f"grader path {spec!r} is missing a module or class name",
            hint="expected the form 'package.module:ClassName'",
        )
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise SelfEvalsUserError(
            f"grader path {spec!r}: module {module_name!r} could not be imported: {exc}",
            hint="is the module importable from your working directory / installed?",
        ) from exc
    target = getattr(module, attribute, None)
    if target is None:
        raise SelfEvalsUserError(
            f"grader path {spec!r}: module {module_name!r} has no attribute {attribute!r}"
        )
    if not (isinstance(target, type) and issubclass(target, Grader)):
        raise SelfEvalsUserError(
            f"grader path {spec!r}: {attribute!r} is not a subclass of Grader"
        )
    try:
        return target()
    except TypeError as exc:
        raise SelfEvalsUserError(
            f"grader path {spec!r}: {attribute!r} could not be constructed with no "
            f"arguments: {exc}",
            hint="custom graders must have a no-arg constructor (give __init__ defaults)",
        ) from exc


def resolve_graders(names: list[str]) -> list[Grader]:
    """Materialise a list of grader instances from names or dotted paths.

    A name containing ``:`` is a ``"module:ClassName"`` dotted path, imported
    and instantiated on demand (no registration needed). Otherwise it is looked
    up in the registry. A missing registry name raises
    :class:`SelfEvalsUserError` with the list of available names — this is the
    path that produces the "Grader 'foo' not registered" message in the
    troubleshooting doc.
    """
    from selfevals.cli._friendly import unknown_grader  # avoid CLI ↔ graders import cycle

    out: list[Grader] = []
    for n in names:
        if ":" in n:
            out.append(_resolve_dotted_grader(n))
            continue
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
register_grader("set_match", SetMatchGrader)
register_grader("trajectory", TrajectoryGrader)
