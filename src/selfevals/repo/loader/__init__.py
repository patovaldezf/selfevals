"""Load an `evals/experiments/*.yaml` spec into Pydantic objects.

The YAML is the authoring surface; the in-memory `ExperimentSpec` is
what the CLI hands to the OptimizationLoop. We intentionally do NOT
introduce a YAML-only DSL — the YAML keys are 1:1 with the field names
on `Experiment`, `EvalCase`, etc. so Pydantic validators do all the
shape checking. The loader's only jobs:

1. Read YAML (and optional JSONL of cases).
2. Hydrate `workspace_id` / generate `id` where missing.
3. Parse the `agent:` block into a typed, transport-tagged spec.

The `agent:` block selects which adapter the CLI wires up. Two YAML
shapes are accepted:

- Legacy: `agent: {entrypoint: "mod:fn"}` → embedded callable.
- Tagged: `agent: {type: embedded|cli|http, ...}`:
    - `embedded` carries `entrypoint: "mod:fn"`.
    - `cli` carries `command: [...]`, optional `env:`, `timeout_seconds:`.
    - `http` carries `url: "..."`, optional `headers:`, `timeout_seconds:`.

The loader stays import-side-effect-free: it only parses and validates
shape. The real adapter construction (importlib, instantiating
`CliCommandAdapter` / `HttpEndpointAdapter`) happens at the wiring point
in `selfevals.cli.commands`.

Split by concern: `models.py` (the typed authoring shapes),
`agent.py` (the `agent:` block), `datasets.py` (the `dataset:` block),
`graders.py` (the `graders:` block). This module hosts the public
load/build/serialize/deserialize entry points and the cross-cutting
`experiment:` / primary-grader validation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from selfevals.repo.loader.agent import build_agent_spec, dump_agent_spec, resolve_agent_callable
from selfevals.repo.loader.datasets import build_dataset_source, dump_dataset_source
from selfevals.repo.loader.graders import build_grader_specs, dump_grader_spec
from selfevals.repo.loader.models import (
    AgentEntrypoint,
    AgentModelDecl,
    AgentSpec,
    CliAgentSpec,
    DatasetSpec,
    EmbeddedAgentSpec,
    ExperimentSpec,
    GraderSpec,
    HttpAgentSpec,
    InlineDatasetSource,
    LoaderError,
    RefDatasetSource,
)
from selfevals.schemas.experiment import Experiment

__all__ = [
    "AgentEntrypoint",
    "AgentModelDecl",
    "AgentSpec",
    "CliAgentSpec",
    "DatasetSpec",
    "EmbeddedAgentSpec",
    "ExperimentSpec",
    "GraderSpec",
    "HttpAgentSpec",
    "InlineDatasetSource",
    "LoaderError",
    "RefDatasetSource",
    "build_spec_from_mapping",
    "deserialize_experiment_spec",
    "load_experiment_spec",
    "resolve_agent_callable",
    "serialize_experiment_spec",
]


def serialize_experiment_spec(spec: ExperimentSpec) -> dict[str, Any]:
    """JSON-safe representation of a fully validated experiment spec.

    The `dataset` block round-trips the spec's `dataset_source`: a
    `RefDatasetSource` serializes to `{ref, version}` (so a worker rehydrating
    the payload resolves the dataset from storage instead of choking on the
    empty inline case list), while an inline source serializes its cases.
    """
    return {
        "workspace": spec.workspace_id,
        "experiment": spec.experiment.model_dump(mode="json"),
        "dataset": dump_dataset_source(spec),
        "agent": dump_agent_spec(spec.agent),
        "graders": [dump_grader_spec(grader) for grader in spec.graders],
    }


def deserialize_experiment_spec(payload: dict[str, Any]) -> ExperimentSpec:
    """Rehydrate a worker-safe spec payload produced by `serialize_experiment_spec`."""
    return build_spec_from_mapping(payload, workspace_id=str(payload.get("workspace") or ""))


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
    return build_spec_from_mapping(raw, workspace_id=workspace_id, source_dir=spec_path.parent)


def build_spec_from_mapping(
    raw: dict[str, Any],
    *,
    workspace_id: str | None = None,
    source_dir: Path | None = None,
) -> ExperimentSpec:
    """Hydrate a fully-validated `ExperimentSpec` from an already-parsed mapping.

    This is the post-parse half of `load_experiment_spec`, exposed so callers
    that receive a spec over the wire (the HTTP `experiments/run` endpoint) can
    reuse the exact same construction and validation without writing the body
    to a temp file.

    `source_dir` is the base for resolving a relative `dataset.cases_path`. When
    it is None (an inline spec with no on-disk home), the spec must carry
    `dataset.cases_inline` instead — `cases_path` would have nothing to resolve
    against and raises a clear `LoaderError`.
    """
    if not isinstance(raw, dict):
        raise LoaderError(f"expected a mapping at the top level, got {type(raw).__name__}")

    ws_id = workspace_id or raw.get("workspace") or raw.get("workspace_id")
    if not ws_id:
        raise LoaderError(
            "workspace_id missing — pass --workspace or set top-level `workspace:` key"
        )

    # `_build_*` use `spec_path` for relative resolution and error labels. With
    # no on-disk source we hand them a sentinel path: `cases_inline` ignores it,
    # and a stray `cases_path` resolves to a non-existent file that errors clearly.
    spec_path = (source_dir / "<inline>") if source_dir is not None else Path("<inline>")

    experiment = _build_experiment(spec_path, raw, ws_id)
    dataset_source = build_dataset_source(spec_path, raw, ws_id)
    # Inline sources carry their cases in the spec; ref sources resolve at
    # launch, so `cases` stays empty until storage hydrates it.
    cases = dataset_source.cases if isinstance(dataset_source, InlineDatasetSource) else []
    agent = build_agent_spec(spec_path, raw)
    graders = build_grader_specs(spec_path, raw)
    _validate_primary_grader(spec_path, experiment, graders, cases)

    return ExperimentSpec(
        workspace_id=ws_id,
        experiment=experiment,
        cases=cases,
        agent=agent,
        dataset_source=dataset_source,
        graders=graders,
    )


def _build_experiment(spec_path: Path, raw: dict[str, Any], workspace_id: str) -> Experiment:
    payload = raw.get("experiment")
    if not isinstance(payload, dict):
        raise LoaderError(f"{spec_path}: missing or non-mapping `experiment:` section")
    payload = dict(payload)
    payload.setdefault("id", Experiment.make_id())
    payload.setdefault("workspace_id", workspace_id)
    try:
        return Experiment(**payload)
    except Exception as exc:  # audit:ignore[broad_exception_catches] — wrap any Pydantic build error into a spec-located LoaderError
        raise LoaderError(f"{spec_path}: invalid experiment payload: {exc}") from exc


def _validate_primary_grader(
    spec_path: Path,
    experiment: Experiment,
    graders: list[GraderSpec],
    cases: list[Any],
) -> None:
    """Ensure `target.primary_grader` names a grader that actually runs.

    The set of grader names available at runtime is the union of: the
    YAML-declared `graders:` specs, any grader a case references by name, and —
    when nothing else is declared — the implicit default `deterministic` grader
    the CLI falls back to (see `_resolve_case_graders`). Checking here (where
    both the experiment and the graders are in hand) rather than inside the
    `Experiment` model keeps the schema decoupled from the grader registry while
    still failing fast on a typo'd primary_grader."""
    primary = experiment.target.primary_grader
    if primary is None:
        return
    names: set[str] = {g.name for g in graders}
    for case in cases:
        names.update(case.graders)
    if not names:
        # No graders declared anywhere -> the CLI runs the implicit default.
        names.add("deterministic")
    if primary not in names:
        raise LoaderError(
            f"{spec_path}: target.primary_grader {primary!r} is not a configured grader. "
            f"Available: {sorted(names)}"
        )
