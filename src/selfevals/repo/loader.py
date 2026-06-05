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
"""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import yaml

from selfevals.schemas.eval_case import EvalCase
from selfevals.schemas.experiment import Experiment


class LoaderError(ValueError):
    """Raised when an experiment spec is malformed or unreadable."""


@dataclass(frozen=True)
class AgentEntrypoint:
    """Resolved location of the user's agent callable.

    The raw string from YAML is `module.path:callable_name`. We split it
    here but defer `importlib.import_module` until the runner actually
    needs to invoke the agent — keeps the loader pure and side-effect
    free, which matters for the `selfevals inspect` flow that just wants
    to read a spec without booting user code.
    """

    raw: str
    module: str
    attribute: str


@dataclass(frozen=True)
class EmbeddedAgentSpec:
    """`agent: {type: embedded, entrypoint: "mod:fn"}` (or legacy shape).

    Carries the parsed `entrypoint`; the callable is resolved at wiring
    time via `resolve_agent_callable` and wrapped in an `EmbeddedAdapter`.
    """

    entrypoint: AgentEntrypoint


@dataclass(frozen=True)
class AgentModelDecl:
    """Optional `agent.model: {provider, name}` for cli/http agents.

    A cli/http agent is a black box — selfevals can't know which model it runs.
    Declaring it here lets selfevals (a) stamp the real model on the trace's LLM
    span instead of "unknown", and (b) price the run from reported tokens when
    the agent doesn't return `cost_usd` itself. Purely advisory: omit it and the
    behaviour is unchanged (model "unknown", cost only when the agent reports it).
    """

    provider: str
    name: str


@dataclass(frozen=True)
class CliAgentSpec:
    """`agent: {type: cli, command: [...], env?, timeout_seconds?, model?}`.

    The CLI wires this into a `CliCommandAdapter` — no Python entrypoint
    proxy needed. `command` is the argv list spawned per case.
    """

    command: list[str]
    env: dict[str, str] | None = None
    timeout_seconds: float | None = None
    model: AgentModelDecl | None = None


@dataclass(frozen=True)
class HttpAgentSpec:
    """`agent: {type: http, url: "...", headers?, timeout_seconds?, model?}`.

    The CLI wires this into an `HttpEndpointAdapter` — no Python
    entrypoint proxy needed.
    """

    url: str
    headers: dict[str, str] | None = None
    timeout_seconds: float | None = None
    model: AgentModelDecl | None = None


AgentSpec = EmbeddedAgentSpec | CliAgentSpec | HttpAgentSpec
"""Transport-tagged agent declaration. The CLI dispatches on the concrete
variant to pick `EmbeddedAdapter` / `CliCommandAdapter` / `HttpEndpointAdapter`."""


@dataclass(frozen=True)
class GraderSpec:
    """Declarative grader configuration.

    YAML shape:
        - type: deterministic
          name: rules                       # optional; defaults per type
        - type: llm_judge
          name: rubric_judge
          rubric: "Was the agent empathetic and accurate?"
          judge_entrypoint: pkg.mod:fn      # optional; falls back to an
                                            # embedded agent's entrypoint

    The instantiator lives in `selfevals.cli.commands` because building an
    `LLMJudgeGrader` requires the same callable-resolution path as the
    main adapter — and the loader stays import-side-effect-free. The
    fallback only works when the agent is `embedded`; cli/http agents must
    name a `judge_entrypoint` explicitly.
    """

    type: str
    name: str
    rubric: str | None = None
    judge_entrypoint: AgentEntrypoint | None = None


@dataclass(frozen=True)
class ExperimentSpec:
    workspace_id: str
    experiment: Experiment
    cases: list[EvalCase]
    agent: AgentSpec
    graders: list[GraderSpec] = field(default_factory=list)


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
    cases = _build_cases(spec_path, raw, ws_id)
    agent = _build_agent_spec(spec_path, raw)
    graders = _build_grader_specs(spec_path, raw)
    _validate_primary_grader(spec_path, experiment, graders, cases)

    return ExperimentSpec(
        workspace_id=ws_id,
        experiment=experiment,
        cases=cases,
        agent=agent,
        graders=graders,
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


def _build_experiment(spec_path: Path, raw: dict[str, Any], workspace_id: str) -> Experiment:
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


def _build_cases(spec_path: Path, raw: dict[str, Any], workspace_id: str) -> list[EvalCase]:
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
            raise LoaderError(f"{spec_path}: case #{i} must be a mapping, got {type(row).__name__}")
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
            raise LoaderError(f"{path}:{line_no}: expected an object, got {type(row).__name__}")
        rows.append(row)
    return rows


_SUPPORTED_GRADER_TYPES = {"deterministic", "llm_judge"}


def _build_grader_specs(spec_path: Path, raw: dict[str, Any]) -> list[GraderSpec]:
    section = raw.get("graders")
    if section is None:
        return []
    if not isinstance(section, list):
        raise LoaderError(f"{spec_path}: `graders:` must be a list of mappings")
    specs: list[GraderSpec] = []
    seen_names: set[str] = set()
    for i, entry in enumerate(section):
        if not isinstance(entry, dict):
            raise LoaderError(
                f"{spec_path}: graders[{i}] must be a mapping, got {type(entry).__name__}"
            )
        type_ = entry.get("type")
        if not isinstance(type_, str) or type_ not in _SUPPORTED_GRADER_TYPES:
            raise LoaderError(
                f"{spec_path}: graders[{i}].type must be one of "
                f"{sorted(_SUPPORTED_GRADER_TYPES)}; got {type_!r}"
            )
        name = entry.get("name") or type_
        if not isinstance(name, str) or not name:
            raise LoaderError(f"{spec_path}: graders[{i}].name must be a non-empty string")
        if name in seen_names:
            raise LoaderError(f"{spec_path}: graders[{i}].name {name!r} is duplicated")
        seen_names.add(name)

        rubric: str | None = None
        judge_entry: AgentEntrypoint | None = None
        if type_ == "llm_judge":
            rubric_raw = entry.get("rubric")
            if not isinstance(rubric_raw, str) or not rubric_raw.strip():
                raise LoaderError(
                    f"{spec_path}: graders[{i}] (llm_judge) requires a non-empty `rubric`"
                )
            rubric = rubric_raw
            judge_raw = entry.get("judge_entrypoint")
            if judge_raw is not None:
                if not isinstance(judge_raw, str) or ":" not in judge_raw:
                    raise LoaderError(
                        f"{spec_path}: graders[{i}].judge_entrypoint must be "
                        f"'module:callable', got {judge_raw!r}"
                    )
                module, _, attribute = judge_raw.partition(":")
                judge_entry = AgentEntrypoint(raw=judge_raw, module=module, attribute=attribute)
        specs.append(GraderSpec(type=type_, name=name, rubric=rubric, judge_entrypoint=judge_entry))
    return specs


def _validate_primary_grader(
    spec_path: Path,
    experiment: Experiment,
    graders: list[GraderSpec],
    cases: list[EvalCase],
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


_SUPPORTED_AGENT_TYPES = {"embedded", "cli", "http"}


def _build_agent_spec(spec_path: Path, raw: dict[str, Any]) -> AgentSpec:
    """Parse the `agent:` block into a transport-tagged spec.

    Accepts the legacy `{entrypoint: ...}` shape (embedded) and the tagged
    `{type: embedded|cli|http, ...}` shape. The type tag selects which
    adapter the CLI wires up; required fields are validated per type so a
    typo surfaces here instead of at adapter-construction time.
    """
    agent_section = raw.get("agent", {})
    if not isinstance(agent_section, dict):
        raise LoaderError(f"{spec_path}: `agent:` must be a mapping")

    type_ = agent_section.get("type")
    if type_ is None:
        # Legacy shape: `agent: {entrypoint: "mod:fn"}` → embedded.
        return EmbeddedAgentSpec(entrypoint=_parse_entrypoint(spec_path, agent_section))

    if not isinstance(type_, str) or type_ not in _SUPPORTED_AGENT_TYPES:
        raise LoaderError(
            f"{spec_path}: agent.type must be one of "
            f"{sorted(_SUPPORTED_AGENT_TYPES)}; got {type_!r}"
        )

    if type_ == "embedded":
        return EmbeddedAgentSpec(entrypoint=_parse_entrypoint(spec_path, agent_section))
    if type_ == "cli":
        return _build_cli_agent_spec(spec_path, agent_section)
    return _build_http_agent_spec(spec_path, agent_section)


def _parse_entrypoint(spec_path: Path, agent_section: dict[str, Any]) -> AgentEntrypoint:
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


def _build_cli_agent_spec(spec_path: Path, agent_section: dict[str, Any]) -> CliAgentSpec:
    if "entrypoint" in agent_section:
        raise LoaderError(
            f"{spec_path}: agent.type 'cli' does not take an `entrypoint`; use `command:` instead"
        )
    command = agent_section.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(c, str) for c in command):
        raise LoaderError(
            f"{spec_path}: agent.type 'cli' requires `command:` as a non-empty list of strings; "
            f"got {command!r}"
        )
    env = _parse_str_map(spec_path, agent_section.get("env"), field_name="agent.env")
    timeout = _parse_timeout(spec_path, agent_section.get("timeout_seconds"))
    model = _parse_agent_model(spec_path, agent_section.get("model"))
    return CliAgentSpec(
        command=[str(c) for c in command], env=env, timeout_seconds=timeout, model=model
    )


def _build_http_agent_spec(spec_path: Path, agent_section: dict[str, Any]) -> HttpAgentSpec:
    if "entrypoint" in agent_section:
        raise LoaderError(
            f"{spec_path}: agent.type 'http' does not take an `entrypoint`; use `url:` instead"
        )
    url = agent_section.get("url")
    if not isinstance(url, str) or not url.strip():
        raise LoaderError(
            f"{spec_path}: agent.type 'http' requires `url:` as a non-empty string; got {url!r}"
        )
    headers = _parse_str_map(spec_path, agent_section.get("headers"), field_name="agent.headers")
    timeout = _parse_timeout(spec_path, agent_section.get("timeout_seconds"))
    model = _parse_agent_model(spec_path, agent_section.get("model"))
    return HttpAgentSpec(url=url, headers=headers, timeout_seconds=timeout, model=model)


def _parse_agent_model(spec_path: Path, value: Any) -> AgentModelDecl | None:
    """Parse the optional `agent.model: {provider, name}` block."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise LoaderError(
            f"{spec_path}: agent.model must be a mapping with `provider` and `name`; got {value!r}"
        )
    provider = value.get("provider")
    name = value.get("name")
    if not isinstance(provider, str) or not provider.strip():
        raise LoaderError(f"{spec_path}: agent.model.provider must be a non-empty string")
    if not isinstance(name, str) or not name.strip():
        raise LoaderError(f"{spec_path}: agent.model.name must be a non-empty string")
    return AgentModelDecl(provider=provider, name=name)


def _parse_str_map(spec_path: Path, value: Any, *, field_name: str) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        raise LoaderError(f"{spec_path}: {field_name} must be a mapping of string→string")
    return {k: v for k, v in value.items()}


def _parse_timeout(spec_path: Path, value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise LoaderError(
            f"{spec_path}: agent.timeout_seconds must be a positive number; got {value!r}"
        )
    return float(value)
