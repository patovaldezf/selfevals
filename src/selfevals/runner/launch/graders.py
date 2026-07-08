"""Register YAML-declared graders into the process-global registry and build
per-type factories (deterministic, llm_judge, judge_panel, pairwise,
set_match, funnel, confusion). Also owns the proposer builder — a small,
unrelated wiring concern that lives here rather than earning its own module.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, cast

from selfevals._errors import SelfEvalsUserError
from selfevals.graders.base import Grader
from selfevals.graders.deterministic import DeterministicGrader
from selfevals.graders.llm_judge import LLMJudgeGrader, RubricTemplate
from selfevals.graders.registry import available_graders, register_grader, resolve_graders
from selfevals.optimization.proposers import (
    GridProposer,
    LLMProposer,
    ManualProposer,
    Proposer,
    RandomProposer,
)
from selfevals.repo.loader import (
    AgentEntrypoint,
    AgentSpec,
    EmbeddedAgentSpec,
    ExperimentSpec,
    LoaderError,
    resolve_agent_callable,
)
from selfevals.runner.adapters import AgentAdapter
from selfevals.runner.launch.adapters import _wrap_user_callable
from selfevals.schemas.enums import ProposerStrategy
from selfevals.schemas.experiment import Experiment


def build_proposer(experiment: Experiment) -> Proposer:
    strategy = experiment.proposer.strategy
    if strategy == ProposerStrategy.GRID:
        return GridProposer()
    if strategy == ProposerStrategy.RANDOM:
        params = dict(experiment.proposer.parameters)
        return RandomProposer(
            max_proposals=int(params.get("max_proposals", 50)),
            seed=params.get("seed"),
        )
    if strategy == ProposerStrategy.MANUAL:
        manual = experiment.proposer.parameters.get("proposals")
        if not isinstance(manual, list) or not manual:
            raise SelfEvalsUserError(
                "manual proposer requires proposer.parameters.proposals as a non-empty list"
            )
        return ManualProposer(manual)
    if strategy == ProposerStrategy.LLM_PROPOSER:
        # Offline by default: deterministically applies seeded hypotheses with
        # no LLM or API key, so a spec can run end-to-end without a network.
        params = dict(experiment.proposer.parameters)
        return LLMProposer(confidence=float(params.get("confidence", 0.5)))
    raise SelfEvalsUserError(f"unsupported proposer strategy: {strategy}")


def register_grader_specs(spec: ExperimentSpec) -> list[str]:
    """Register YAML-declared graders into the global registry.

    Returns the list of names that were registered so the caller can
    unregister them when done — keeping the registry hermetic across
    consecutive runs.

    Deterministic specs override the default factory with one that uses the
    declared name. LLM-judge specs require a `judge_entrypoint` (or fall back to
    the agent entrypoint) so the rubric grader can invoke a real callable.
    """
    registered: list[str] = []
    for g_spec in spec.graders:
        if g_spec.type == "deterministic":
            register_grader(g_spec.name, _deterministic_factory(g_spec.name))
            registered.append(g_spec.name)
            continue
        if g_spec.type == "llm_judge":
            entry = g_spec.judge_entrypoint or _agent_entrypoint_for_judge(g_spec.name, spec.agent)
            try:
                judge_callable = resolve_agent_callable(entry)
            except LoaderError as exc:
                raise SelfEvalsUserError(str(exc)) from exc
            judge_adapter = _wrap_user_callable(judge_callable, entry)
            rubric = g_spec.rubric or ""
            register_grader(g_spec.name, _llm_judge_factory(g_spec.name, judge_adapter, rubric))
            registered.append(g_spec.name)
            continue
        if g_spec.type == "judge_panel":
            entry = g_spec.judge_entrypoint or _agent_entrypoint_for_judge(g_spec.name, spec.agent)
            try:
                judge_callable = resolve_agent_callable(entry)
            except LoaderError as exc:
                raise SelfEvalsUserError(str(exc)) from exc
            judge_adapter = _wrap_user_callable(judge_callable, entry)
            register_grader(
                g_spec.name,
                _judge_panel_factory(
                    g_spec.name,
                    judge_adapter,
                    g_spec.rubric or "",
                    n_judges=g_spec.n_judges or 3,
                    consensus=g_spec.consensus or "majority",
                ),
            )
            registered.append(g_spec.name)
            continue
        if g_spec.type == "pairwise":
            entry = g_spec.judge_entrypoint or _agent_entrypoint_for_judge(g_spec.name, spec.agent)
            try:
                judge_callable = resolve_agent_callable(entry)
            except LoaderError as exc:
                raise SelfEvalsUserError(str(exc)) from exc
            judge_adapter = _wrap_user_callable(judge_callable, entry)
            register_grader(
                g_spec.name,
                _pairwise_factory(g_spec.name, judge_adapter, g_spec.rubric or "", g_spec.params),
            )
            registered.append(g_spec.name)
            continue
        if g_spec.type == "set_match":
            register_grader(g_spec.name, _set_match_factory(g_spec.name, g_spec.params))
            registered.append(g_spec.name)
            continue
        if g_spec.type == "funnel":
            register_grader(g_spec.name, _funnel_factory(g_spec.name, g_spec.params))
            registered.append(g_spec.name)
            continue
        if g_spec.type == "confusion":
            register_grader(g_spec.name, _confusion_factory(g_spec.name, g_spec.params))
            registered.append(g_spec.name)
            continue
        raise SelfEvalsUserError(f"unsupported grader type: {g_spec.type!r}")  # defensive
    return registered


def _deterministic_factory(name: str) -> Callable[[], Grader]:
    def _build() -> Grader:
        return DeterministicGrader(name=name)

    return _build


def _llm_judge_factory(name: str, judge_adapter: AgentAdapter, rubric: str) -> Callable[[], Grader]:
    template = RubricTemplate(rubric=rubric)

    def _build() -> Grader:
        return LLMJudgeGrader(name=name, judge_adapter=judge_adapter, rubric=template)

    return _build


def _judge_panel_factory(
    name: str,
    judge_adapter: AgentAdapter,
    rubric: str,
    *,
    n_judges: int,
    consensus: str,
) -> Callable[[], Grader]:
    """Build a panel of `n_judges` identical-rubric judges + a consensus rule.

    The judges share one rubric and one adapter — independence comes from the
    LLM's own sampling, not from distinct prompts. Each judge needs a unique
    name within the panel (a `JudgePanelGrader` invariant), so they are suffixed
    `-judge-0..N`. Default consensus is `majority`; for `weighted` (which the
    panel requires explicit weights for) we pass uniform weights so a bare
    `consensus: weighted` still constructs.
    """
    from selfevals.graders.judge_panel import JudgePanelGrader

    template = RubricTemplate(rubric=rubric)

    def _build() -> Grader:
        judges: list[Grader] = [
            LLMJudgeGrader(name=f"{name}-judge-{k}", judge_adapter=judge_adapter, rubric=template)
            for k in range(n_judges)
        ]
        weights = [1.0] * n_judges if consensus == "weighted" else None
        return JudgePanelGrader(name=name, judges=judges, consensus_rule=consensus, weights=weights)

    return _build


def _pairwise_factory(
    name: str,
    judge_adapter: AgentAdapter,
    rubric: str,
    params: dict[str, object],
) -> Callable[[], Grader]:
    """Build a PairwiseGrader (LLM head-to-head judge) from a YAML spec.

    Reuses the same judge-adapter resolution as `llm_judge` (an explicit
    `judge_entrypoint` or the embedded-agent fallback). The grader's behavior
    flags come from the validated `params` bag (`compare_against`/`tie_is_pass`/
    `swap_and_average`); loader validation guarantees their types.
    """
    from selfevals.graders.pairwise import PairwiseGrader, PairwiseRubric

    template = PairwiseRubric(rubric=rubric)
    compare_against = cast("Any", params.get("compare_against", "reference"))
    tie_is_pass = bool(params.get("tie_is_pass", True))
    swap_and_average = bool(params.get("swap_and_average", False))

    def _build() -> Grader:
        return PairwiseGrader(
            name=name,
            judge_adapter=judge_adapter,
            rubric=template,
            compare_against=compare_against,
            tie_is_pass=tie_is_pass,
            swap_and_average=swap_and_average,
        )

    return _build


def _set_match_factory(name: str, params: dict[str, object]) -> Callable[[], Grader]:
    from selfevals.graders.set_match import GatingDimension, SetMatchGrader

    gating = cast("GatingDimension", params.get("gating", "completeness"))
    threshold = float(cast(float, params.get("threshold", 1.0)))
    case_sensitive = bool(params.get("case_sensitive", False))

    def _build() -> Grader:
        return SetMatchGrader(
            name=name, gating=gating, threshold=threshold, case_sensitive=case_sensitive
        )

    return _build


def _confusion_factory(name: str, params: dict[str, object]) -> Callable[[], Grader]:
    from selfevals.graders.classification import ClassificationGrader

    extract = str(params.get("extract", "label"))
    expected_from_raw = params.get("expected_from")
    expected_from = str(expected_from_raw) if expected_from_raw is not None else None
    case_sensitive = bool(params.get("case_sensitive", False))

    def _build() -> Grader:
        return ClassificationGrader(
            name=name,
            extract=extract,
            expected_from=expected_from,
            case_sensitive=case_sensitive,
        )

    return _build


def _funnel_factory(name: str, params: dict[str, object]) -> Callable[[], Grader]:
    """Build a FunnelGrader from the loader's validated level dicts.

    Level construction happens inside `_build()` (not here) so each
    `resolve_case_graders` call gets fresh instances and so registry lookups for
    nested `grader:` references resolve after every factory has registered —
    declaration order between the funnel and the graders it references does not
    matter (mirrors how `resolve_case_graders` instantiates lazily).
    """
    from selfevals.graders.funnel import (
        FunnelGrader,
        _ByIndexMatch,
        _ByKeyMatch,
        _EqualsMatch,
        _ExistsMatch,
        _Level,
        _SpanExistsMatch,
        _ToolCalledMatch,
    )
    from selfevals.graders.set_match import GatingDimension, SetMatchGrader
    from selfevals.schemas.enums import SpanKind

    levels_spec = cast("list[dict[str, object]]", params.get("levels", []))

    def _default_fm(key: str, kind: str) -> str:
        suffix = {
            "exists": "absent",
            "equals": "mismatch",
            "by_key": "key_mismatch",
            "by_index": "index_mismatch",
            "tool_called": "tool_absent",
            "span_exists": "span_absent",
        }.get(kind, "fail")
        return f"funnel_{key}_{suffix}"

    def _build_match(key: str, extract: str, match: dict[str, object]) -> Grader:
        match_name = f"{name}.{key}"
        if "grader" in match:
            ref = cast(str, match["grader"])
            return resolve_graders([ref])[0]
        kind = cast(str, match["kind"])
        cs = bool(match.get("case_sensitive", False))
        fm = _default_fm(key, kind)
        if kind == "set_match":
            gating = cast("GatingDimension", match.get("gating", "completeness"))
            threshold = float(cast(float, match.get("threshold", 1.0)))
            # An omitted level `extract` ("") means "the default detected slot"
            # for set_match, matching the standalone grader's `extract="detected"`
            # default — without this a bare `kind: set_match` level would select
            # the root dict and always FAIL.
            sm_extract = extract or "detected"
            return SetMatchGrader(
                name=match_name,
                gating=gating,
                threshold=threshold,
                case_sensitive=cs,
                extract=sm_extract,
            )
        if kind == "exists":
            return _ExistsMatch(match_name, extract=extract, failure_mode=fm)
        if kind == "equals":
            return _EqualsMatch(
                match_name,
                extract=extract,
                value=match["value"],
                case_sensitive=cs,
                failure_mode=fm,
            )
        if kind == "by_key":
            return _ByKeyMatch(
                match_name,
                extract=extract,
                key=cast(str, match["key"]),
                value=match["value"],
                case_sensitive=cs,
                failure_mode=fm,
            )
        if kind == "by_index":
            return _ByIndexMatch(
                match_name,
                extract=extract,
                index=cast(int, match["index"]),
                value=match["value"],
                case_sensitive=cs,
                failure_mode=fm,
            )
        if kind == "tool_called":
            return _ToolCalledMatch(match_name, tool=cast(str, match["tool"]), failure_mode=fm)
        if kind == "span_exists":
            return _SpanExistsMatch(
                match_name, span_kind=SpanKind(cast(str, match["span_kind"])), failure_mode=fm
            )
        raise SelfEvalsUserError(f"funnel: unknown match kind {kind!r}")  # defensive

    def _build_level(node: dict[str, object]) -> _Level:
        key = cast(str, node["key"])
        extract = cast(str, node.get("extract", ""))
        match = _build_match(key, extract, cast("dict[str, object]", node["match"]))
        children_spec = cast("list[dict[str, object]]", node.get("children", []))
        return _Level(
            key=key,
            extract=extract,
            match=match,
            gate=bool(node.get("gate", False)),
            failure_mode=cast("str | None", node.get("failure_mode")),
            feeds_extract=bool(node.get("feeds_extract", False)),
            children=[_build_level(c) for c in children_spec],
        )

    def _build() -> Grader:
        return FunnelGrader(name=name, levels=[_build_level(node) for node in levels_spec])

    return _build


def resolve_case_graders(cases: Sequence[object]) -> list[Grader]:
    """Build the grader list the loop will run for every case.

    The default behaviour (no case declares a `graders:` list) is unchanged
    from the original `[DeterministicGrader()]`. As soon as a case names graders
    by string we route through the registry, which is the codepath that raises
    the "not registered" friendly error.
    """
    referenced: list[str] = []
    for case in cases:
        names = getattr(case, "graders", None) or []
        for n in names:
            if n not in referenced:
                referenced.append(n)
    if not referenced:
        return [DeterministicGrader()]
    # `resolve_graders` raises SelfEvalsUserError if any name is unknown,
    # listing the registry contents — the user-facing "unknown grader" path.
    _ = available_graders()  # cheap, also guarantees registry import side effects.
    return list(resolve_graders(referenced))


def _agent_entrypoint_for_judge(grader_name: str, agent: AgentSpec) -> AgentEntrypoint:
    """Resolve the judge fallback to the agent's entrypoint.

    The `judge_entrypoint`-omitted fallback only makes sense for an embedded
    agent — a cli/http agent has no in-process callable to reuse as a judge.
    """
    if isinstance(agent, EmbeddedAgentSpec):
        return agent.entrypoint
    raise SelfEvalsUserError(
        f"grader {grader_name!r} (llm_judge) has no `judge_entrypoint` and the agent is not "
        f"embedded ({type(agent).__name__}); add an explicit `judge_entrypoint: 'mod:fn'`"
    )
