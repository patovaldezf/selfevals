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

from selfevals.schemas._base import EntityRef
from selfevals.schemas.dataset import SplitAllocation
from selfevals.schemas.enums import DatasetType, SpanKind
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
        - type: set_match                   # many-to-many set scoring
          name: intention_f1
          params: {gating: f1, threshold: 0.8}   # optional; default completeness@1.0
        - type: llm_judge
          name: rubric_judge
          rubric: "Was the agent empathetic and accurate?"
          judge_entrypoint: pkg.mod:fn      # optional; falls back to an
                                            # embedded agent's entrypoint
        - type: judge_panel                 # N judges + consensus
          name: quality_panel
          rubric: "Score 0-1: is the answer correct and grounded?"
          n_judges: 3                        # optional; default 3 (odd → no ties)
          consensus: majority                # majority | unanimous | weighted
          judge_entrypoint: pkg.mod:fn       # optional; falls back like llm_judge

    The instantiator lives in `selfevals.cli.commands` because building an
    `LLMJudgeGrader` requires the same callable-resolution path as the
    main adapter — and the loader stays import-side-effect-free. The
    fallback only works when the agent is `embedded`; cli/http agents must
    name a `judge_entrypoint` explicitly.

    `params` is a generic bag for grader-type-specific tuning (e.g. set_match's
    `gating`/`threshold`) so adding a tunable grader does not require touching
    this dataclass each time.
    """

    type: str
    name: str
    rubric: str | None = None
    judge_entrypoint: AgentEntrypoint | None = None
    n_judges: int | None = None
    consensus: str | None = None
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InlineDatasetSource:
    """`dataset: {cases_inline | cases_path, name?, dataset_type?, ...}`.

    The cases are declared in the spec itself (inline list or a JSONL path).
    The loader parses them into `cases`; `runner.launch` materializes a real
    `Dataset` entity over them at run time (the loader stays storage-free). The
    optional `name` / `dataset_type` / `split_allocation` / `description` carry
    the manifest metadata for that materialization, with sensible defaults when
    omitted so existing specs keep working unchanged.
    """

    cases: list[EvalCase]
    name: str | None = None
    dataset_type: DatasetType | None = None
    split_allocation: SplitAllocation | None = None
    description: str | None = None


@dataclass(frozen=True)
class RefDatasetSource:
    """`dataset: {ref: ds_xxx, version?}` — reference a persisted Dataset.

    No cases are declared in the spec; `runner.launch` resolves the referenced
    `Dataset` from storage, hydrates its cases, and adopts its split allocation.
    This is how one standalone dataset is reused across many experiments. A ref
    cannot be resolved without persistence, so an ephemeral run (`--no-persist`)
    over a ref is a user error, raised at launch.
    """

    ref: EntityRef


DatasetSpec = InlineDatasetSource | RefDatasetSource
"""Tagged declaration of where an experiment's cases come from. `runner.launch`
dispatches on the concrete variant: materialize (inline) vs resolve (ref)."""


@dataclass(frozen=True)
class ExperimentSpec:
    workspace_id: str
    experiment: Experiment
    cases: list[EvalCase]
    agent: AgentSpec
    dataset_source: DatasetSpec
    graders: list[GraderSpec] = field(default_factory=list)


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
        "dataset": _dump_dataset_source(spec),
        "agent": _dump_agent_spec(spec.agent),
        "graders": [_dump_grader_spec(grader) for grader in spec.graders],
    }


def _dump_dataset_source(spec: ExperimentSpec) -> dict[str, Any]:
    """Serialize the spec's dataset source for `serialize_experiment_spec`."""
    if isinstance(spec.dataset_source, RefDatasetSource):
        ref = spec.dataset_source.ref
        block: dict[str, Any] = {"ref": ref.id}
        if ref.version is not None:
            block["version"] = ref.version
        return block
    return {"cases_inline": [case.model_dump(mode="json") for case in spec.cases]}


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
    dataset_source = _build_dataset_source(spec_path, raw, ws_id)
    # Inline sources carry their cases in the spec; ref sources resolve at
    # launch, so `cases` stays empty until storage hydrates it.
    cases = (
        dataset_source.cases if isinstance(dataset_source, InlineDatasetSource) else []
    )
    agent = _build_agent_spec(spec_path, raw)
    graders = _build_grader_specs(spec_path, raw)
    _validate_primary_grader(spec_path, experiment, graders, cases)

    return ExperimentSpec(
        workspace_id=ws_id,
        experiment=experiment,
        cases=cases,
        agent=agent,
        dataset_source=dataset_source,
        graders=graders,
    )


def _dump_entrypoint(entrypoint: AgentEntrypoint) -> str:
    return entrypoint.raw


def _dump_agent_model(model: AgentModelDecl | None) -> dict[str, str] | None:
    if model is None:
        return None
    return {"provider": model.provider, "name": model.name}


def _dump_agent_spec(agent: AgentSpec) -> dict[str, Any]:
    if isinstance(agent, EmbeddedAgentSpec):
        return {"type": "embedded", "entrypoint": _dump_entrypoint(agent.entrypoint)}
    if isinstance(agent, CliAgentSpec):
        payload: dict[str, Any] = {"type": "cli", "command": list(agent.command)}
        if agent.env is not None:
            payload["env"] = dict(agent.env)
        if agent.timeout_seconds is not None:
            payload["timeout_seconds"] = agent.timeout_seconds
        if agent.model is not None:
            payload["model"] = _dump_agent_model(agent.model)
        return payload
    payload = {"type": "http", "url": agent.url}
    if agent.headers is not None:
        payload["headers"] = dict(agent.headers)
    if agent.timeout_seconds is not None:
        payload["timeout_seconds"] = agent.timeout_seconds
    if agent.model is not None:
        payload["model"] = _dump_agent_model(agent.model)
    return payload


def _dump_grader_spec(grader: GraderSpec) -> dict[str, Any]:
    payload: dict[str, Any] = {"type": grader.type, "name": grader.name}
    if grader.rubric is not None:
        payload["rubric"] = grader.rubric
    if grader.judge_entrypoint is not None:
        payload["judge_entrypoint"] = _dump_entrypoint(grader.judge_entrypoint)
    if grader.n_judges is not None:
        payload["n_judges"] = grader.n_judges
    if grader.consensus is not None:
        payload["consensus"] = grader.consensus
    if grader.params:
        payload["params"] = dict(grader.params)
    return payload


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


def _build_dataset_source(
    spec_path: Path, raw: dict[str, Any], workspace_id: str
) -> DatasetSpec:
    """Classify the `dataset:` block into a tagged source (no storage access).

    Three shapes, mutually exclusive:
    - `cases_inline:` / `cases_path:` → `InlineDatasetSource` (cases declared
      here; optional `name`/`dataset_type`/`split_allocation`/`description`
      describe the Dataset that launch will materialize).
    - `ref:` → `RefDatasetSource` (reuse a persisted Dataset; resolved at launch).
    Mixing `ref:` with inline cases is rejected, mirroring the inline XOR.
    """
    dataset = raw.get("dataset", {})
    if not isinstance(dataset, dict):
        raise LoaderError(f"{spec_path}: `dataset:` must be a mapping")

    ref = dataset.get("ref")
    inline = dataset.get("cases_inline")
    cases_path = dataset.get("cases_path")
    has_inline = inline is not None or cases_path is not None

    if ref is not None and has_inline:
        raise LoaderError(
            f"{spec_path}: dataset cannot mix `ref:` with `cases_inline:`/`cases_path:` "
            "— a ref reuses a persisted dataset, inline declares its own cases"
        )
    if ref is not None:
        return _build_ref_dataset_source(spec_path, dataset, ref)

    cases = _build_cases(spec_path, dataset, workspace_id)
    return InlineDatasetSource(
        cases=cases,
        name=_opt_str(spec_path, dataset, "name"),
        dataset_type=_opt_dataset_type(spec_path, dataset),
        split_allocation=_opt_split_allocation(spec_path, dataset),
        description=_opt_str(spec_path, dataset, "description"),
    )


def _build_ref_dataset_source(
    spec_path: Path, dataset: dict[str, Any], ref: Any
) -> RefDatasetSource:
    if not isinstance(ref, str) or not ref:
        raise LoaderError(f"{spec_path}: `dataset.ref:` must be a non-empty dataset id string")
    version = dataset.get("version")
    if version is not None and not isinstance(version, int):
        raise LoaderError(f"{spec_path}: `dataset.version:` must be an integer when given")
    try:
        return RefDatasetSource(ref=EntityRef(id=ref, version=version))
    except Exception as exc:
        raise LoaderError(f"{spec_path}: invalid `dataset.ref:`: {exc}") from exc


def _opt_str(spec_path: Path, dataset: dict[str, Any], key: str) -> str | None:
    value = dataset.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise LoaderError(f"{spec_path}: `dataset.{key}:` must be a non-empty string when given")
    return value


def _opt_dataset_type(spec_path: Path, dataset: dict[str, Any]) -> DatasetType | None:
    value = dataset.get("dataset_type")
    if value is None:
        return None
    try:
        return DatasetType(value)
    except ValueError as exc:
        raise LoaderError(f"{spec_path}: invalid `dataset.dataset_type:` {value!r}: {exc}") from exc


def _opt_split_allocation(spec_path: Path, dataset: dict[str, Any]) -> SplitAllocation | None:
    value = dataset.get("split_allocation")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise LoaderError(f"{spec_path}: `dataset.split_allocation:` must be a mapping when given")
    try:
        return SplitAllocation(**value)
    except Exception as exc:
        raise LoaderError(f"{spec_path}: invalid `dataset.split_allocation:`: {exc}") from exc


def _build_cases(spec_path: Path, dataset: dict[str, Any], workspace_id: str) -> list[EvalCase]:
    inline = dataset.get("cases_inline")
    cases_path = dataset.get("cases_path")
    if inline is None and cases_path is None:
        raise LoaderError(
            f"{spec_path}: dataset must provide `cases_inline:`, `cases_path:`, or `ref:`"
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


_SUPPORTED_GRADER_TYPES = {
    "deterministic",
    "llm_judge",
    "judge_panel",
    "pairwise",
    "set_match",
    "funnel",
    "confusion",
}
_PAIRWISE_COMPARE_AGAINST = {"reference"}
_SET_MATCH_GATINGS = {"completeness", "precision", "recall", "f1"}
_CONSENSUS_RULES = {"majority", "unanimous", "weighted"}
_FUNNEL_MATCH_KINDS = {
    "exists",
    "equals",
    "set_match",
    "by_key",
    "by_index",
    "tool_called",
    "span_exists",
}
_SPAN_KINDS = {k.value for k in SpanKind}


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
        n_judges: int | None = None
        consensus: str | None = None
        params: dict[str, Any] = {}

        if type_ in ("llm_judge", "judge_panel", "pairwise"):
            rubric_raw = entry.get("rubric")
            if not isinstance(rubric_raw, str) or not rubric_raw.strip():
                raise LoaderError(
                    f"{spec_path}: graders[{i}] ({type_}) requires a non-empty `rubric`"
                )
            rubric = rubric_raw
            judge_entry = _parse_judge_entrypoint(spec_path, i, entry)

        if type_ == "judge_panel":
            n_judges = _parse_n_judges(spec_path, i, entry)
            consensus = _parse_consensus(spec_path, i, entry)

        if type_ == "pairwise":
            params = _parse_pairwise_params(spec_path, i, entry)

        if type_ == "set_match":
            params = _parse_set_match_params(spec_path, i, entry)

        if type_ == "funnel":
            params = _parse_funnel_params(spec_path, i, entry)

        if type_ == "confusion":
            params = _parse_confusion_params(spec_path, i, entry)

        specs.append(
            GraderSpec(
                type=type_,
                name=name,
                rubric=rubric,
                judge_entrypoint=judge_entry,
                n_judges=n_judges,
                consensus=consensus,
                params=params,
            )
        )
    return specs


def _parse_judge_entrypoint(
    spec_path: Path, i: int, entry: dict[str, Any]
) -> AgentEntrypoint | None:
    judge_raw = entry.get("judge_entrypoint")
    if judge_raw is None:
        return None
    if not isinstance(judge_raw, str) or ":" not in judge_raw:
        raise LoaderError(
            f"{spec_path}: graders[{i}].judge_entrypoint must be "
            f"'module:callable', got {judge_raw!r}"
        )
    module, _, attribute = judge_raw.partition(":")
    return AgentEntrypoint(raw=judge_raw, module=module, attribute=attribute)


def _parse_n_judges(spec_path: Path, i: int, entry: dict[str, Any]) -> int:
    raw = entry.get("n_judges", 3)
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 1:
        raise LoaderError(
            f"{spec_path}: graders[{i}].n_judges must be an integer >= 1, got {raw!r}"
        )
    return raw


def _parse_consensus(spec_path: Path, i: int, entry: dict[str, Any]) -> str:
    raw = entry.get("consensus", "majority")
    if raw not in _CONSENSUS_RULES:
        raise LoaderError(
            f"{spec_path}: graders[{i}].consensus must be one of "
            f"{sorted(_CONSENSUS_RULES)}, got {raw!r}"
        )
    return cast(str, raw)


def _parse_set_match_params(spec_path: Path, i: int, entry: dict[str, Any]) -> dict[str, Any]:
    raw = entry.get("params", {})
    if not isinstance(raw, dict):
        raise LoaderError(f"{spec_path}: graders[{i}].params must be a mapping, got {raw!r}")
    params: dict[str, Any] = {}
    gating = raw.get("gating")
    if gating is not None:
        if gating not in _SET_MATCH_GATINGS:
            raise LoaderError(
                f"{spec_path}: graders[{i}].params.gating must be one of "
                f"{sorted(_SET_MATCH_GATINGS)}, got {gating!r}"
            )
        params["gating"] = gating
    threshold = raw.get("threshold")
    if threshold is not None:
        if not isinstance(threshold, int | float) or isinstance(threshold, bool):
            raise LoaderError(
                f"{spec_path}: graders[{i}].params.threshold must be a number, got {threshold!r}"
            )
        if not 0.0 <= float(threshold) <= 1.0:
            raise LoaderError(
                f"{spec_path}: graders[{i}].params.threshold must be in [0, 1], got {threshold!r}"
            )
        params["threshold"] = float(threshold)
    case_sensitive = raw.get("case_sensitive")
    if case_sensitive is not None:
        if not isinstance(case_sensitive, bool):
            raise LoaderError(
                f"{spec_path}: graders[{i}].params.case_sensitive must be a boolean, "
                f"got {case_sensitive!r}"
            )
        params["case_sensitive"] = case_sensitive
    return params


def _parse_pairwise_params(spec_path: Path, i: int, entry: dict[str, Any]) -> dict[str, Any]:
    """Validate a `pairwise` grader's params.

    `compare_against` selects what B is (MVP: only `reference`). `tie_is_pass`
    maps a tie verdict to PASS (default true). `swap_and_average` runs A/B and
    B/A and averages to neutralize the judge's position bias (default false).
    """
    raw = entry.get("params", {})
    if not isinstance(raw, dict):
        raise LoaderError(f"{spec_path}: graders[{i}].params must be a mapping, got {raw!r}")
    params: dict[str, Any] = {}
    compare_against = raw.get("compare_against")
    if compare_against is not None:
        if compare_against not in _PAIRWISE_COMPARE_AGAINST:
            raise LoaderError(
                f"{spec_path}: graders[{i}].params.compare_against must be one of "
                f"{sorted(_PAIRWISE_COMPARE_AGAINST)}, got {compare_against!r}"
            )
        params["compare_against"] = compare_against
    for flag in ("tie_is_pass", "swap_and_average"):
        value = raw.get(flag)
        if value is not None:
            if not isinstance(value, bool):
                raise LoaderError(
                    f"{spec_path}: graders[{i}].params.{flag} must be a boolean, got {value!r}"
                )
            params[flag] = value
    return params


def _parse_confusion_params(spec_path: Path, i: int, entry: dict[str, Any]) -> dict[str, Any]:
    """Validate a `confusion` grader's params (`extract`/`expected_from`/`case_sensitive`).

    `extract` is the path selector into `structured_output` for the predicted
    class (default `"label"`); `expected_from`, when set, is the path into
    `Expected.structured_output` for the ground-truth class (default: read
    `Expected.outcome`). Paths are validated here so a YAML typo fails at load.
    """
    from selfevals.graders._select import validate_path

    raw = entry.get("params", {})
    if not isinstance(raw, dict):
        raise LoaderError(f"{spec_path}: graders[{i}].params must be a mapping, got {raw!r}")
    params: dict[str, Any] = {}
    extract = raw.get("extract")
    if extract is not None:
        if not isinstance(extract, str):
            raise LoaderError(
                f"{spec_path}: graders[{i}].params.extract must be a string, got {extract!r}"
            )
        try:
            validate_path(extract)
        except ValueError as exc:
            raise LoaderError(f"{spec_path}: graders[{i}].params.extract: {exc}") from exc
        params["extract"] = extract
    expected_from = raw.get("expected_from")
    if expected_from is not None:
        if not isinstance(expected_from, str):
            raise LoaderError(
                f"{spec_path}: graders[{i}].params.expected_from must be a string, "
                f"got {expected_from!r}"
            )
        try:
            validate_path(expected_from)
        except ValueError as exc:
            raise LoaderError(f"{spec_path}: graders[{i}].params.expected_from: {exc}") from exc
        params["expected_from"] = expected_from
    case_sensitive = raw.get("case_sensitive")
    if case_sensitive is not None:
        if not isinstance(case_sensitive, bool):
            raise LoaderError(
                f"{spec_path}: graders[{i}].params.case_sensitive must be a boolean, "
                f"got {case_sensitive!r}"
            )
        params["case_sensitive"] = case_sensitive
    return params


def _parse_funnel_params(spec_path: Path, i: int, entry: dict[str, Any]) -> dict[str, Any]:
    """Parse a funnel's `params.levels` tree into validated nested dicts.

    Stored as plain JSON-friendly dicts (not built `_Level`s) because building
    a level's match — especially a nested `llm_judge` — needs adapter
    resolution, which lives in `runner.launch`, not here. The loader stays
    import-side-effect-free. `key` uniqueness is checked across the whole tree
    (the aggregator rolls up by key).
    """
    from selfevals.graders._select import validate_path

    raw = entry.get("params", {})
    if not isinstance(raw, dict):
        raise LoaderError(f"{spec_path}: graders[{i}].params must be a mapping, got {raw!r}")
    levels_raw = raw.get("levels")
    if not isinstance(levels_raw, list) or not levels_raw:
        raise LoaderError(
            f"{spec_path}: graders[{i}] (funnel) requires a non-empty `params.levels` list"
        )
    seen_keys: set[str] = set()

    def _level(node: Any, path: str) -> dict[str, Any]:
        if not isinstance(node, dict):
            raise LoaderError(f"{spec_path}: graders[{i}].levels{path} must be a mapping")
        key = node.get("key")
        if not isinstance(key, str) or not key:
            raise LoaderError(
                f"{spec_path}: graders[{i}].levels{path}.key must be a non-empty string"
            )
        if key in seen_keys:
            raise LoaderError(
                f"{spec_path}: graders[{i}].levels key {key!r} is duplicated (keys must be "
                f"unique across the funnel tree)"
            )
        seen_keys.add(key)

        extract = node.get("extract", "")
        if not isinstance(extract, str):
            raise LoaderError(f"{spec_path}: graders[{i}].levels[{key}].extract must be a string")
        try:
            validate_path(extract)
        except ValueError as exc:
            raise LoaderError(f"{spec_path}: graders[{i}].levels[{key}].extract: {exc}") from exc

        gate = node.get("gate", False)
        if not isinstance(gate, bool):
            raise LoaderError(f"{spec_path}: graders[{i}].levels[{key}].gate must be a boolean")
        feeds = node.get("feeds_extract", False)
        if not isinstance(feeds, bool):
            raise LoaderError(
                f"{spec_path}: graders[{i}].levels[{key}].feeds_extract must be a boolean"
            )
        failure_mode = node.get("failure_mode")
        if failure_mode is not None and not (isinstance(failure_mode, str) and failure_mode):
            raise LoaderError(
                f"{spec_path}: graders[{i}].levels[{key}].failure_mode must be a non-empty string"
            )

        match = _funnel_match(spec_path, i, key, node.get("match"))

        children_raw = node.get("children", [])
        if not isinstance(children_raw, list):
            raise LoaderError(f"{spec_path}: graders[{i}].levels[{key}].children must be a list")
        children = [_level(c, f"{path}[{key}].children[{j}]") for j, c in enumerate(children_raw)]

        out: dict[str, Any] = {
            "key": key,
            "extract": extract,
            "gate": gate,
            "feeds_extract": feeds,
            "match": match,
            "children": children,
        }
        if failure_mode is not None:
            out["failure_mode"] = failure_mode
        return out

    levels = [_level(node, f"[{j}]") for j, node in enumerate(levels_raw)]
    return {"levels": levels}


def _funnel_match(spec_path: Path, i: int, key: str, match_raw: Any) -> dict[str, Any]:
    """Validate one level's `match`: exactly one of `kind` / `grader`."""
    if not isinstance(match_raw, dict):
        raise LoaderError(f"{spec_path}: graders[{i}].levels[{key}].match must be a mapping")
    has_kind = "kind" in match_raw
    has_grader = "grader" in match_raw
    if has_kind == has_grader:
        raise LoaderError(
            f"{spec_path}: graders[{i}].levels[{key}].match must have exactly one of "
            f"`kind` (builtin) or `grader` (a declared grader name)"
        )
    if has_grader:
        ref = match_raw.get("grader")
        if not isinstance(ref, str) or not ref:
            raise LoaderError(
                f"{spec_path}: graders[{i}].levels[{key}].match.grader must be a non-empty string"
            )
        # Existence deferred to launch.py (registry resolution gives the friendly
        # unknown-grader error and avoids a declaration-ordering constraint).
        return {"grader": ref}

    kind = match_raw.get("kind")
    if kind not in _FUNNEL_MATCH_KINDS:
        raise LoaderError(
            f"{spec_path}: graders[{i}].levels[{key}].match.kind must be one of "
            f"{sorted(_FUNNEL_MATCH_KINDS)}; got {kind!r}"
        )
    out: dict[str, Any] = {"kind": kind}
    # Carry through kind-specific params, validated where it matters.
    if kind == "equals":
        if "value" not in match_raw:
            raise LoaderError(
                f"{spec_path}: graders[{i}].levels[{key}].match (equals) requires `value`"
            )
        out["value"] = match_raw["value"]
    elif kind == "by_key":
        bkey = match_raw.get("key")
        if not isinstance(bkey, str) or not bkey:
            raise LoaderError(
                f"{spec_path}: graders[{i}].levels[{key}].match (by_key) requires a string `key`"
            )
        if "value" not in match_raw:
            raise LoaderError(
                f"{spec_path}: graders[{i}].levels[{key}].match (by_key) requires `value`"
            )
        out["key"] = bkey
        out["value"] = match_raw["value"]
    elif kind == "by_index":
        index = match_raw.get("index")
        if not isinstance(index, int) or isinstance(index, bool):
            raise LoaderError(
                f"{spec_path}: graders[{i}].levels[{key}].match (by_index) requires an int `index`"
            )
        if "value" not in match_raw:
            raise LoaderError(
                f"{spec_path}: graders[{i}].levels[{key}].match (by_index) requires `value`"
            )
        out["index"] = index
        out["value"] = match_raw["value"]
    elif kind == "tool_called":
        tool = match_raw.get("tool")
        if not isinstance(tool, str) or not tool:
            raise LoaderError(
                f"{spec_path}: graders[{i}].levels[{key}].match (tool_called) requires a "
                f"string `tool`"
            )
        out["tool"] = tool
    elif kind == "span_exists":
        span_kind = match_raw.get("span_kind")
        if span_kind not in _SPAN_KINDS:
            raise LoaderError(
                f"{spec_path}: graders[{i}].levels[{key}].match (span_exists) requires "
                f"`span_kind` in {sorted(_SPAN_KINDS)}; got {span_kind!r}"
            )
        out["span_kind"] = span_kind
    elif kind == "set_match":
        # Validate the same way the standalone set_match grader does, so a funnel
        # level can't smuggle a bad gating/threshold/case_sensitive past the loader
        # only to crash (uncaught) when the grader is instantiated at launch.
        gating = match_raw.get("gating")
        if gating is not None and gating not in _SET_MATCH_GATINGS:
            raise LoaderError(
                f"{spec_path}: graders[{i}].levels[{key}].match.gating must be one of "
                f"{sorted(_SET_MATCH_GATINGS)}; got {gating!r}"
            )
        if gating is not None:
            out["gating"] = gating
        threshold = match_raw.get("threshold")
        if threshold is not None:
            if not isinstance(threshold, int | float) or isinstance(threshold, bool):
                raise LoaderError(
                    f"{spec_path}: graders[{i}].levels[{key}].match.threshold must be a number; "
                    f"got {threshold!r}"
                )
            if not 0.0 <= float(threshold) <= 1.0:
                raise LoaderError(
                    f"{spec_path}: graders[{i}].levels[{key}].match.threshold must be in [0, 1]; "
                    f"got {threshold!r}"
                )
            out["threshold"] = float(threshold)
    case_sensitive = match_raw.get("case_sensitive")
    if kind in ("equals", "by_key", "by_index", "set_match") and case_sensitive is not None:
        if not isinstance(case_sensitive, bool):
            raise LoaderError(
                f"{spec_path}: graders[{i}].levels[{key}].match.case_sensitive must be a boolean"
            )
        out["case_sensitive"] = case_sensitive
    return out


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
