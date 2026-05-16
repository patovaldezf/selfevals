"""Proposers: how candidate proposals are generated for an Experiment.

A `Proposer` is a stateful generator: `propose(experiment, history)`
returns the next `Proposal`, or raises `SearchSpaceExhaustedError` if
there's nothing left to try. The loop uses that signal to terminate.

MVP ships three strategies. Each respects the experiment's
`editable` contract via `Proposal.validate_against(experiment)` — the
loop also re-validates as a belt-and-suspenders check.

- `ManualProposer`: walks a caller-supplied list of parameter dicts.
- `GridProposer`: cartesian product over `search_space.model_params`
  values that are lists.
- `RandomProposer`: independent uniform samples from each parameter's
  declared range (numeric `[lo, hi]`) or list (categorical). Seeded.
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod
from dataclasses import dataclass
from itertools import product
from typing import TYPE_CHECKING, Any

from bootstrap.schemas.iteration import Proposal

if TYPE_CHECKING:
    from bootstrap.schemas.experiment import Experiment
    from bootstrap.schemas.iteration import IterationRecord


class SearchSpaceExhaustedError(StopIteration):
    """Raised when a Proposer has nothing left to propose."""


@dataclass(frozen=True)
class ProposerContext:
    iteration_index: int
    history: tuple[IterationRecord, ...] = ()


class Proposer(ABC):
    name: str

    @abstractmethod
    def propose(
        self, experiment: Experiment, context: ProposerContext
    ) -> Proposal: ...


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
            raise SearchSpaceExhaustedError(
                f"grid exhausted after {len(combos)} combinations"
            )
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
            raise SearchSpaceExhaustedError(
                f"random proposer exhausted after {self._max} samples"
            )
        params = {
            k: _sample_value(self._rng, v)
            for k, v in experiment.search_space.model_params.items()
        }
        proposal = Proposal(
            parameters={"model_params": params},
            hypothesis=f"random[{context.iteration_index}]: {params}",
        )
        _validate_or_raise(proposal, experiment)
        return proposal
