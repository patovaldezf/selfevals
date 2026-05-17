"""Load an `evals/experiments/*.yaml` spec into Pydantic objects.

The YAML is the authoring surface; the in-memory `ExperimentSpec` is
what the CLI hands to the OptimizationLoop. We intentionally do NOT
introduce a YAML-only DSL — the YAML keys are 1:1 with the field names
on `Experiment`, `EvalCase`, etc. so Pydantic validators do all the
shape checking. The loader's only jobs:

1. Read YAML (and optional JSONL of cases).
2. Hydrate `workspace_id` / generate `id` where missing.
3. Resolve the agent entrypoint string into a callable on demand.
"""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml

from bootstrap.schemas.eval_case import EvalCase
from bootstrap.schemas.experiment import Experiment


class LoaderError(ValueError):
    """Raised when an experiment spec is malformed or unreadable."""


@dataclass(frozen=True)
class AgentEntrypoint:
    """Resolved location of the user's agent callable.

    The raw string from YAML is `module.path:callable_name`. We split it
    here but defer `importlib.import_module` until the runner actually
    needs to invoke the agent — keeps the loader pure and side-effect
    free, which matters for the `bootstrap inspect` flow that just wants
    to read a spec without booting user code.
    """

    raw: str
    module: str
    attribute: str


@dataclass(frozen=True)
class ExperimentSpec:
    workspace_id: str
    experiment: Experiment
    cases: list[EvalCase]
    agent: AgentEntrypoint


def load_experiment_spec(
    path: str | Path,
    *,
    workspace_id: str | None = None,
) -> ExperimentSpec:
    """Read a YAML spec from disk and return a fully-validated bundle."""
    spec_path = Path(path)
    if not spec_path.exists():
        raise LoaderError(f"experiment spec not found: {spec_path}")
    try:
        raw = yaml.safe_load(spec_path.read_text())
    except yaml.YAMLError as exc:
        raise LoaderError(f"could not parse YAML {spec_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise LoaderError(
            f"{spec_path}: expected a mapping at the top level, got {type(raw).__name__}"
        )

    ws_id = workspace_id or raw.get("workspace") or raw.get("workspace_id")
    if not ws_id:
        raise LoaderError(
            f"{spec_path}: workspace_id missing — pass --workspace or set top-level `workspace:` key"
        )

    experiment = _build_experiment(spec_path, raw, ws_id)
    cases = _build_cases(spec_path, raw, ws_id)
    agent = _build_agent_entrypoint(spec_path, raw)

    return ExperimentSpec(
        workspace_id=ws_id,
        experiment=experiment,
        cases=cases,
        agent=agent,
    )


def resolve_agent_callable(entrypoint: AgentEntrypoint) -> Any:
    """Import `entrypoint.module` and return the named attribute.

    Kept separate from spec loading so callers can validate a spec
    without triggering user-code import side effects.
    """
    try:
        module = importlib.import_module(entrypoint.module)
    except ImportError as exc:
        raise LoaderError(
            f"agent entrypoint module {entrypoint.module!r} could not be imported: {exc}"
        ) from exc
    try:
        return getattr(module, entrypoint.attribute)
    except AttributeError as exc:
        raise LoaderError(
            f"agent entrypoint {entrypoint.raw!r}: "
            f"module {entrypoint.module!r} has no attribute {entrypoint.attribute!r}"
        ) from exc


# --- internals ---


def _build_experiment(
    spec_path: Path, raw: dict[str, Any], workspace_id: str
) -> Experiment:
    payload = raw.get("experiment")
    if not isinstance(payload, dict):
        raise LoaderError(f"{spec_path}: missing or non-mapping `experiment:` section")
    payload = dict(payload)
    payload.setdefault("id", Experiment.make_id())
    payload.setdefault("workspace_id", workspace_id)
    try:
        return Experiment(**payload)
    except Exception as exc:
        raise LoaderError(f"{spec_path}: invalid experiment payload: {exc}") from exc


def _build_cases(
    spec_path: Path, raw: dict[str, Any], workspace_id: str
) -> list[EvalCase]:
    dataset = raw.get("dataset", {})
    if not isinstance(dataset, dict):
        raise LoaderError(f"{spec_path}: `dataset:` must be a mapping")

    inline = dataset.get("cases_inline")
    cases_path = dataset.get("cases_path")
    if inline is None and cases_path is None:
        raise LoaderError(
            f"{spec_path}: dataset must provide either `cases_inline:` or `cases_path:`"
        )
    if inline is not None and cases_path is not None:
        raise LoaderError(
            f"{spec_path}: dataset cannot have both `cases_inline:` and `cases_path:`"
        )

    if inline is not None:
        if not isinstance(inline, list):
            raise LoaderError(f"{spec_path}: `cases_inline:` must be a list")
        rows = [cast(dict[str, Any], row) for row in inline]
    else:
        rows = _read_jsonl(spec_path.parent / str(cases_path))

    if not rows:
        raise LoaderError(f"{spec_path}: dataset yielded zero cases")

    cases: list[EvalCase] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise LoaderError(
                f"{spec_path}: case #{i} must be a mapping, got {type(row).__name__}"
            )
        case_payload = dict(row)
        case_payload.setdefault("id", EvalCase.make_id())
        case_payload.setdefault("workspace_id", workspace_id)
        try:
            cases.append(EvalCase(**case_payload))
        except Exception as exc:
            raise LoaderError(f"{spec_path}: invalid case #{i}: {exc}") from exc
    return cases


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise LoaderError(f"dataset file not found: {path}")
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text().splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            row = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise LoaderError(f"{path}:{line_no}: invalid JSON: {exc}") from exc
        if not isinstance(row, dict):
            raise LoaderError(
                f"{path}:{line_no}: expected an object, got {type(row).__name__}"
            )
        rows.append(row)
    return rows


def _build_agent_entrypoint(spec_path: Path, raw: dict[str, Any]) -> AgentEntrypoint:
    agent_section = raw.get("agent", {})
    if not isinstance(agent_section, dict):
        raise LoaderError(f"{spec_path}: `agent:` must be a mapping")
    entrypoint = agent_section.get("entrypoint")
    if not isinstance(entrypoint, str) or ":" not in entrypoint:
        raise LoaderError(
            f"{spec_path}: agent.entrypoint must be a string of the form "
            f"'package.module:callable_name'; got {entrypoint!r}"
        )
    module, _, attribute = entrypoint.partition(":")
    if not module or not attribute:
        raise LoaderError(
            f"{spec_path}: agent.entrypoint {entrypoint!r} is missing module or callable name"
        )
    return AgentEntrypoint(raw=entrypoint, module=module, attribute=attribute)
