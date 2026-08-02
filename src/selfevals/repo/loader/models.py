"""Typed authoring models: the shapes `ExperimentSpec` is built from.

These are dataclasses, not Pydantic models — the YAML shape validation lives
in the `_build_*`/`_parse_*` functions across this package (each raises a
spec-located `LoaderError`), so the dataclasses themselves only need to carry
already-validated data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from selfevals.schemas._base import EntityRef
from selfevals.schemas.dataset import SplitAllocation
from selfevals.schemas.enums import DatasetType
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
    """`agent: {type: embedded, entrypoint: "mod:fn", timeout_seconds?}`.

    Carries the parsed `entrypoint`; the callable is resolved at wiring
    time via `resolve_agent_callable` and wrapped in an `EmbeddedAdapter`.
    """

    entrypoint: AgentEntrypoint
    timeout_seconds: float | None = None
    """Per-case wall-clock cap, mirroring the cli/http specs. `None` waits
    forever — right for a pure function, wrong for a callable that drives a
    browser or a device: those *wedge* instead of failing, and a wedged case
    holds its concurrency slot for the rest of the run. Timeouts are retryable,
    so a transient hang costs one retry rather than the case."""


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
    """`agent: {type: cli, command: [...], env?, timeout_seconds?, model?, cwd?}`.

    The CLI wires this into a `CliCommandAdapter` — no Python entrypoint
    proxy needed. `command` is the argv list spawned per case. `cwd` is an
    optional absolute working directory for the subprocess — Arena uses it
    to point a variant's command at its own git worktree checkout; omitted,
    the subprocess inherits the caller's cwd (unchanged default behaviour).
    """

    command: list[str]
    env: dict[str, str] | None = None
    timeout_seconds: float | None = None
    model: AgentModelDecl | None = None
    cwd: str | None = None


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
    cannot be resolved without persistence, so an ephemeral run (no scope)
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
