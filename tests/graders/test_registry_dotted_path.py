"""Fix D — declarative custom graders via `module:ClassName` dotted paths.

`resolve_graders` accepts a name containing `:` as a dotted import path,
imports the class on demand, validates it subclasses `Grader`, and
instantiates it — no `register_grader` side-effect import needed. Built-in
names (no `:`) keep the registry path. The two coexist.
"""

from __future__ import annotations

import sys
import types

import pytest

from selfevals._errors import SelfEvalsUserError
from selfevals.graders.base import GradeLabel, Grader, GraderContext, GradeResult
from selfevals.graders.registry import resolve_graders


class _OkGrader(Grader):
    name = "ok_custom"

    async def grade(self, context: GraderContext) -> GradeResult:  # pragma: no cover - not run here
        return GradeResult(grader=self.name, label=GradeLabel.PASS, reason="ok")


class _NeedsArgGrader(Grader):
    name = "needs_arg"

    def __init__(self, k: int) -> None:  # no default → not constructible no-arg
        self.k = k

    async def grade(self, context: GraderContext) -> GradeResult:  # pragma: no cover
        return GradeResult(grader=self.name, label=GradeLabel.PASS, reason="x")


class _NotAGrader:
    pass


@pytest.fixture
def fixture_module() -> str:
    """Register an in-memory module exposing the test graders, so a dotted path
    like 'mod:Class' resolves without touching the on-disk package."""
    mod_name = "selfevals_test_dotted_graders"
    mod = types.ModuleType(mod_name)
    mod.OkGrader = _OkGrader  # type: ignore[attr-defined]
    mod.NeedsArgGrader = _NeedsArgGrader  # type: ignore[attr-defined]
    mod.NotAGrader = _NotAGrader  # type: ignore[attr-defined]
    sys.modules[mod_name] = mod
    try:
        yield mod_name
    finally:
        sys.modules.pop(mod_name, None)


def test_dotted_path_resolves_and_instantiates(fixture_module: str) -> None:
    graders = resolve_graders([f"{fixture_module}:OkGrader"])
    assert len(graders) == 1
    assert isinstance(graders[0], _OkGrader)
    assert graders[0].name == "ok_custom"


def test_dotted_path_coexists_with_builtin(fixture_module: str) -> None:
    # A built-in registry name and a dotted path resolve together.
    graders = resolve_graders(["deterministic", f"{fixture_module}:OkGrader"])
    assert len(graders) == 2
    assert isinstance(graders[1], _OkGrader)


def test_dotted_path_bad_module() -> None:
    with pytest.raises(SelfEvalsUserError, match="could not be imported"):
        resolve_graders(["no.such.module:Whatever"])


def test_dotted_path_missing_attribute(fixture_module: str) -> None:
    with pytest.raises(SelfEvalsUserError, match="has no attribute"):
        resolve_graders([f"{fixture_module}:DoesNotExist"])


def test_dotted_path_not_a_grader(fixture_module: str) -> None:
    with pytest.raises(SelfEvalsUserError, match="not a subclass of Grader"):
        resolve_graders([f"{fixture_module}:NotAGrader"])


def test_dotted_path_requires_no_arg_constructor(fixture_module: str) -> None:
    with pytest.raises(SelfEvalsUserError, match="no-arg constructor"):
        resolve_graders([f"{fixture_module}:NeedsArgGrader"])


def test_malformed_dotted_path() -> None:
    with pytest.raises(SelfEvalsUserError, match="missing a module or class name"):
        resolve_graders(["justthis:"])
