"""Proposers: how candidate proposals are generated for an Experiment.

A `Proposer` is a stateful generator: `propose(experiment, context)`
returns the next `Proposal` (or an awaitable of one), or raises
`SearchSpaceExhaustedError` if there's nothing left to try. The loop
uses that signal to terminate.

Each proposer respects the experiment's `editable` contract via
`Proposal.validate_against(experiment)` — the loop also re-validates as a
belt-and-suspenders check.

- `ManualProposer`: walks a caller-supplied list of parameter dicts.
- `GridProposer`: cartesian product over `search_space.model_params`
  values that are lists.
- `RandomProposer`: independent uniform samples from each parameter's
  declared range (numeric `[lo, hi]`) or list (categorical). Seeded.
- `LLMProposer`: reads the dominant failure modes and the not-yet-applied
  `HypothesisRecord`s carried in `ProposerContext`. With an `AgentAdapter`
  injected it asks an LLM for the next change; offline (the default, used
  by tests) it deterministically applies the next pending hypothesis.

`propose` may be synchronous or return an awaitable: the loop awaits the
result when it is awaitable. `LLMProposer` uses this so its LLM path can
`await` the adapter natively while the offline path stays plain-sync.
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from itertools import product
from typing import TYPE_CHECKING, Any

from selfevals.schemas.iteration import Proposal

if TYPE_CHECKING:
    from selfevals.analysis.hypothesis import HypothesisRecord
    from selfevals.runner.adapters import AgentAdapter
    from selfevals.schemas.experiment import Experiment
    from selfevals.schemas.iteration import IterationRecord


class SearchSpaceExhaustedError(StopIteration):
    """Raised when a Proposer has nothing left to propose."""


@dataclass(frozen=True)
class ProposerContext:
    """What a Proposer is shown before emitting the next Proposal.

    `failure_modes` are the dominant mode identities carried over from the
    prior iteration (frequency-ordered); `pending_hypotheses` are the
    `HypothesisRecord`s seeded by error analysis that no proposer has applied
    yet. Both default to empty so the deterministic proposers ignore them.
    See docs/spec/error_analysis_design.md §7.
    """

    iteration_index: int
    history: tuple[IterationRecord, ...] = ()
    failure_modes: tuple[str, ...] = ()
    pending_hypotheses: tuple[HypothesisRecord, ...] = field(default=())


class Proposer(ABC):
    name: str

    @abstractmethod
    def propose(
        self, experiment: Experiment, context: ProposerContext
    ) -> Proposal | Awaitable[Proposal]: ...


def _validate_or_raise(proposal: Proposal, experiment: Experiment) -> None:
    proposal.validate_against(experiment)


class ManualProposer(Proposer):
    """Walk through a fixed list of pre-built proposals (or parameter dicts).

    Useful for canary runs and tests. When the list is exhausted,
    raises `SearchSpaceExhaustedError`.
    """

    name = "manual"

    def __init__(self, proposals: list[Proposal] | list[dict[str, Any]]) -> None:
        if not proposals:
            raise ValueError("ManualProposer requires at least one proposal")
        coerced: list[Proposal] = []
        for p in proposals:
            if isinstance(p, Proposal):
                coerced.append(p)
                continue
            if not isinstance(p, dict):
                raise TypeError(
                    f"ManualProposer entries must be Proposal or dict, got {type(p).__name__}"
                )
            hypothesis = str(p.get("hypothesis") or "manual proposal")
            coerced.append(
                Proposal(
                    parameters={k: v for k, v in p.items() if k != "hypothesis"},
                    hypothesis=hypothesis,
                )
            )
        self._proposals = coerced

    def propose(self, experiment: Experiment, context: ProposerContext) -> Proposal:
        if context.iteration_index >= len(self._proposals):
            raise SearchSpaceExhaustedError(
                f"manual proposer exhausted after {len(self._proposals)} proposals"
            )
        proposal = self._proposals[context.iteration_index]
        _validate_or_raise(proposal, experiment)
        return proposal


def _grid_combinations(search_space: dict[str, Any]) -> list[dict[str, Any]]:
    """Cartesian product over list-valued entries in `search_space`.

    Non-list entries are treated as constants and included in every
    combination. Empty list values produce zero combinations.
    """
    keys: list[str] = []
    values: list[list[Any]] = []
    constants: dict[str, Any] = {}
    for k, v in search_space.items():
        if isinstance(v, list):
            keys.append(k)
            values.append(v)
        else:
            constants[k] = v
    if not values:
        return [dict(constants)] if constants else []
    combos: list[dict[str, Any]] = []
    for combo in product(*values):
        d = dict(constants)
        for k, val in zip(keys, combo, strict=True):
            d[k] = val
        combos.append(d)
    return combos


class GridProposer(Proposer):
    """Cartesian product over `experiment.search_space.model_params`.

    A list value enumerates choices; a scalar value is held constant.
    Each combination becomes one Proposal. Raises
    `SearchSpaceExhaustedError` once all combinations are emitted.
    """

    name = "grid"

    def __init__(self) -> None:
        self._combos: list[dict[str, Any]] | None = None

    def _ensure_combos(self, experiment: Experiment) -> list[dict[str, Any]]:
        if self._combos is None:
            self._combos = _grid_combinations(experiment.search_space.model_params)
            if not self._combos:
                raise ValueError(
                    "grid proposer requires at least one combination in "
                    "experiment.search_space.model_params"
                )
        return self._combos

    def propose(self, experiment: Experiment, context: ProposerContext) -> Proposal:
        combos = self._ensure_combos(experiment)
        if context.iteration_index >= len(combos):
            raise SearchSpaceExhaustedError(f"grid exhausted after {len(combos)} combinations")
        params = combos[context.iteration_index]
        proposal = Proposal(
            parameters={"model_params": params},
            hypothesis=f"grid[{context.iteration_index}]: {params}",
        )
        _validate_or_raise(proposal, experiment)
        return proposal


def _sample_value(rng: random.Random, spec: Any) -> Any:
    """Sample one value from a search-space spec.

    - list → pick one element uniformly.
    - {"lo", "hi"} numeric range → uniform float in [lo, hi].
    - {"choices": [...]} → pick from list.
    - scalar → return as-is (constant).
    """
    if isinstance(spec, list):
        if not spec:
            raise ValueError("cannot sample from empty list")
        return rng.choice(spec)
    if isinstance(spec, dict):
        if "choices" in spec:
            return rng.choice(spec["choices"])
        if "lo" in spec and "hi" in spec:
            return rng.uniform(float(spec["lo"]), float(spec["hi"]))
    return spec


class RandomProposer(Proposer):
    """Independent uniform samples from each entry in `model_params`.

    Bounded by `max_proposals`; raises `SearchSpaceExhaustedError` once
    that many have been emitted. Seeded for reproducibility.
    """

    name = "random"

    def __init__(self, *, max_proposals: int = 50, seed: int | None = None) -> None:
        if max_proposals < 1:
            raise ValueError("max_proposals must be >= 1")
        self._max = max_proposals
        self._rng = random.Random(seed)

    def propose(self, experiment: Experiment, context: ProposerContext) -> Proposal:
        if context.iteration_index >= self._max:
            raise SearchSpaceExhaustedError(f"random proposer exhausted after {self._max} samples")
        params = {
            k: _sample_value(self._rng, v) for k, v in experiment.search_space.model_params.items()
        }
        proposal = Proposal(
            parameters={"model_params": params},
            hypothesis=f"random[{context.iteration_index}]: {params}",
        )
        _validate_or_raise(proposal, experiment)
        return proposal


def _next_pending_hypothesis(context: ProposerContext) -> HypothesisRecord | None:
    """First hypothesis in `context.pending_hypotheses` not yet applied.

    `pending_hypotheses` is already ordered by the caller; we honour that
    order and skip any record a prior iteration consumed (defensive — the
    loop filters consumed records out before building the context)."""
    for hyp in context.pending_hypotheses:
        if hyp.consumed_by_iteration is None:
            return hyp
    return None


def _proposal_from_hypothesis(hyp: HypothesisRecord, *, confidence: float) -> Proposal:
    """Turn a HypothesisRecord into a Proposal the loop can run.

    The hypothesis' `suggested_parameters` become the proposal parameters,
    its `statement` becomes the hypothesis text, and the mode it targets is
    referenced so the before/after on `failure_mode_counts` is interpretable.
    """
    return Proposal(
        parameters=dict(hyp.suggested_parameters),
        hypothesis=hyp.statement,
        confidence=confidence,
        inputs_referenced=[hyp.targets_mode_slug],
    )


class LLMProposer(Proposer):
    """Propose the next change from analysis evidence.

    Two modes share one contract:

    - **Offline (default, no adapter)** — deterministic and the path tests
      exercise. Applies the next not-yet-consumed `HypothesisRecord` from
      `context.pending_hypotheses`: its `suggested_parameters` become the
      Proposal, its `statement` the hypothesis, and the record is stamped
      `consumed_by_iteration` so it is never re-applied. Raises
      `SearchSpaceExhaustedError` once no pending hypotheses remain — the
      same convergence signal the other proposers use.
    - **LLM (adapter injected)** — asks the agent for the next change given
      the dominant failure modes + pending hypotheses, then parses the
      `structured_output` of the `AdapterResponse` into a Proposal. The
      adapter's `invoke` is async, so `propose` returns an awaitable here;
      the loop awaits it.

    `on_consume` is an optional callback the caller wires to persist a
    consumed `HypothesisRecord` (e.g. `scope.put_entity`). The proposer
    stays agnostic: it never imports storage.
    """

    name = "llm"

    def __init__(
        self,
        *,
        adapter: AgentAdapter | None = None,
        confidence: float = 0.5,
        on_consume: Callable[[HypothesisRecord], None] | None = None,
    ) -> None:
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be in [0.0, 1.0]")
        self._adapter = adapter
        self._confidence = confidence
        self._on_consume = on_consume

    def propose(
        self, experiment: Experiment, context: ProposerContext
    ) -> Proposal | Awaitable[Proposal]:
        if self._adapter is not None:
            return self._propose_with_llm(experiment, context, self._adapter)
        return self._propose_offline(experiment, context)

    def _propose_offline(self, experiment: Experiment, context: ProposerContext) -> Proposal:
        hyp = _next_pending_hypothesis(context)
        if hyp is None:
            raise SearchSpaceExhaustedError(
                "llm proposer (offline) exhausted: no pending hypotheses remain"
            )
        proposal = _proposal_from_hypothesis(hyp, confidence=self._confidence)
        _validate_or_raise(proposal, experiment)
        self._mark_consumed(hyp, context.iteration_index)
        return proposal

    async def _propose_with_llm(
        self, experiment: Experiment, context: ProposerContext, adapter: AgentAdapter
    ) -> Proposal:
        from selfevals.runner.adapters import AdapterRequest

        request = AdapterRequest(
            workspace_id=experiment.workspace_id,
            case_id=experiment.id,
            input=_proposer_prompt(experiment, context),
            metadata={"role": "llm_proposer", "iteration_index": context.iteration_index},
        )
        response = await adapter.invoke(request)
        proposal = _proposal_from_structured_output(response.structured_output, self._confidence)
        _validate_or_raise(proposal, experiment)
        # If the LLM chose to act on a pending hypothesis, retire it so a later
        # iteration's offline fallback doesn't replay it.
        hyp = _next_pending_hypothesis(context)
        if hyp is not None:
            self._mark_consumed(hyp, context.iteration_index)
        return proposal

    def _mark_consumed(self, hyp: HypothesisRecord, iteration_index: int) -> None:
        hyp.consumed_by_iteration = iteration_index
        if self._on_consume is not None:
            self._on_consume(hyp)


def _proposer_prompt(experiment: Experiment, context: ProposerContext) -> dict[str, Any]:
    """Build the evidence payload handed to the LLM adapter.

    A plain JSON-able dict: the experiment goal, the dominant failure modes,
    and the pending hypotheses. The adapter is free to render this however it
    likes; selfevals only contracts the structured response it parses back."""
    return {
        "task": "propose_next_change",
        "goal": experiment.goal,
        "editable": experiment.editable.model_dump(),
        "search_space": experiment.search_space.model_dump(),
        "failure_modes": list(context.failure_modes),
        "pending_hypotheses": [
            {
                "targets_mode_slug": h.targets_mode_slug,
                "statement": h.statement,
                "suggested_parameters": dict(h.suggested_parameters),
            }
            for h in context.pending_hypotheses
            if h.consumed_by_iteration is None
        ],
    }


def _proposal_from_structured_output(
    structured_output: dict[str, Any] | None, default_confidence: float
) -> Proposal:
    """Parse an adapter's `structured_output` into a Proposal.

    Expects `{parameters: {...}, hypothesis: str, confidence?: float}`.
    Raises `LLMProposalError` when the response is missing or malformed —
    selfevals does not silently fabricate a proposal."""
    if not isinstance(structured_output, dict):
        raise LLMProposalError(
            "llm proposer requires a structured_output object from the adapter; "
            f"got {type(structured_output).__name__}"
        )
    hypothesis = structured_output.get("hypothesis")
    if not isinstance(hypothesis, str) or not hypothesis.strip():
        raise LLMProposalError("llm proposer response is missing a non-empty 'hypothesis'")
    parameters = structured_output.get("parameters", {})
    if not isinstance(parameters, dict):
        raise LLMProposalError("llm proposer response 'parameters' must be an object")
    confidence_raw = structured_output.get("confidence", default_confidence)
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError) as exc:
        raise LLMProposalError("llm proposer response 'confidence' must be a number") from exc
    inputs_referenced = structured_output.get("inputs_referenced", [])
    if not isinstance(inputs_referenced, list):
        raise LLMProposalError("llm proposer response 'inputs_referenced' must be a list")
    return Proposal(
        parameters=dict(parameters),
        hypothesis=hypothesis,
        confidence=confidence,
        inputs_referenced=[str(x) for x in inputs_referenced],
    )


class LLMProposalError(ValueError):
    """Raised when an LLM adapter's response can't be parsed into a Proposal."""
