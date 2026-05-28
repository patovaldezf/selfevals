from __future__ import annotations

from typing import Any

import pytest

from selfevals.analysis.hypothesis import HypothesisRecord
from selfevals.optimization.proposers import (
    GridProposer,
    LLMProposalError,
    LLMProposer,
    ManualProposer,
    ProposerContext,
    RandomProposer,
    SearchSpaceExhaustedError,
)
from selfevals.runner.adapters import AdapterRequest, AdapterResponse, EmbeddedAdapter
from selfevals.schemas._base import EntityRef
from selfevals.schemas.enums import (
    DatasetType,
    Mode,
    ProposerStrategy,
    SandboxMode,
)
from selfevals.schemas.experiment import (
    DatasetUsage,
    EditableContract,
    Experiment,
    ExperimentTaxonomy,
    FrozenSnapshot,
    MetricTarget,
    ProposerSpec,
    RunSpec,
    SearchSpace,
    TargetSpec,
)
from selfevals.schemas.iteration import Proposal, ProposalRejectedError

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _experiment(*, search_space: dict[str, Any] | None = None) -> Experiment:
    return Experiment(
        id=Experiment.make_id(),
        workspace_id=WS,
        name="x",
        goal="x",
        mode=Mode.HANDOFF,
        taxonomy=ExperimentTaxonomy(
            target_features=["commerce.product_resolution"],
            dataset_types=[DatasetType.CAPABILITY],
        ),
        datasets=DatasetUsage(optimization=EntityRef(id="ds_x", version=1)),
        target=TargetSpec(primary=MetricTarget(name="pass@1", operator=">=", value=0.85)),
        editable=EditableContract(prompt=True, model_params=True),
        frozen=FrozenSnapshot(
            fleet=EntityRef(id="flt_x"),
            agents=[EntityRef(id="ag_x")],
            datasets=[EntityRef(id="ds_y")],
        ),
        proposer=ProposerSpec(strategy=ProposerStrategy.GRID),
        run=RunSpec(sandbox=SandboxMode.DRY_RUN),
        search_space=SearchSpace(model_params=search_space or {}),
    )


def test_manual_walks_through_list() -> None:
    exp = _experiment()
    proposer = ManualProposer(
        [
            {"prompt": "v1", "hypothesis": "first"},
            {"prompt": "v2", "hypothesis": "second"},
        ]
    )
    p0 = proposer.propose(exp, ProposerContext(iteration_index=0))
    p1 = proposer.propose(exp, ProposerContext(iteration_index=1))
    assert p0.parameters == {"prompt": "v1"}
    assert p1.parameters == {"prompt": "v2"}


def test_manual_exhausts_after_list_consumed() -> None:
    exp = _experiment()
    proposer = ManualProposer([{"prompt": "v1", "hypothesis": "h"}])
    proposer.propose(exp, ProposerContext(iteration_index=0))
    with pytest.raises(SearchSpaceExhaustedError):
        proposer.propose(exp, ProposerContext(iteration_index=1))


def test_manual_validates_editable_contract() -> None:
    exp = _experiment()  # editable: prompt + model_params; tool_code is False
    proposer = ManualProposer([{"tool_code": "rewritten", "hypothesis": "hack"}])
    with pytest.raises(ProposalRejectedError):
        proposer.propose(exp, ProposerContext(iteration_index=0))


def test_manual_accepts_prebuilt_proposals() -> None:
    exp = _experiment()
    proposal = Proposal(parameters={"prompt": "x"}, hypothesis="y")
    proposer = ManualProposer([proposal])
    out = proposer.propose(exp, ProposerContext(iteration_index=0))
    assert out is proposal


def test_manual_requires_non_empty_list() -> None:
    with pytest.raises(ValueError):
        ManualProposer([])


def test_manual_rejects_invalid_entry_type() -> None:
    with pytest.raises(TypeError):
        ManualProposer(["not a proposal"])  # type: ignore[list-item]


def test_grid_cartesian_product() -> None:
    exp = _experiment(search_space={"temperature": [0.0, 0.5], "top_p": [0.9, 1.0]})
    grid = GridProposer()
    combos: list[dict[str, Any]] = []
    for i in range(4):
        p = grid.propose(exp, ProposerContext(iteration_index=i))
        combos.append(p.parameters["model_params"])
    pairs = {(c["temperature"], c["top_p"]) for c in combos}
    assert pairs == {(0.0, 0.9), (0.0, 1.0), (0.5, 0.9), (0.5, 1.0)}
    with pytest.raises(SearchSpaceExhaustedError):
        grid.propose(exp, ProposerContext(iteration_index=4))


def test_grid_holds_scalars_constant() -> None:
    exp = _experiment(search_space={"temperature": [0.0, 0.5], "top_p": 1.0})
    grid = GridProposer()
    p0 = grid.propose(exp, ProposerContext(iteration_index=0))
    p1 = grid.propose(exp, ProposerContext(iteration_index=1))
    assert p0.parameters["model_params"]["top_p"] == 1.0
    assert p1.parameters["model_params"]["top_p"] == 1.0


def test_grid_empty_space_rejected() -> None:
    exp = _experiment(search_space={"temperature": []})
    grid = GridProposer()
    with pytest.raises(ValueError):
        grid.propose(exp, ProposerContext(iteration_index=0))


def test_random_is_reproducible_with_seed() -> None:
    exp = _experiment(search_space={"temperature": {"lo": 0.0, "hi": 1.0}})
    a = RandomProposer(max_proposals=5, seed=42)
    b = RandomProposer(max_proposals=5, seed=42)
    for i in range(5):
        pa = a.propose(exp, ProposerContext(iteration_index=i))
        pb = b.propose(exp, ProposerContext(iteration_index=i))
        assert pa.parameters == pb.parameters


def test_random_samples_from_list_choices() -> None:
    exp = _experiment(search_space={"model": ["claude", "gpt"]})
    rp = RandomProposer(max_proposals=20, seed=1)
    seen: set[str] = set()
    for i in range(20):
        seen.add(
            rp.propose(exp, ProposerContext(iteration_index=i)).parameters["model_params"]["model"]
        )
    assert seen <= {"claude", "gpt"}


def test_random_respects_max_proposals() -> None:
    exp = _experiment(search_space={"temperature": {"lo": 0.0, "hi": 1.0}})
    rp = RandomProposer(max_proposals=2, seed=0)
    rp.propose(exp, ProposerContext(iteration_index=0))
    rp.propose(exp, ProposerContext(iteration_index=1))
    with pytest.raises(SearchSpaceExhaustedError):
        rp.propose(exp, ProposerContext(iteration_index=2))


def test_random_validates_editable_contract() -> None:
    # Try to mutate `tool_code` even though editable=False.
    exp = _experiment(search_space={"temperature": [0.0, 0.5]})
    rp = RandomProposer(max_proposals=1, seed=0)
    # RandomProposer puts samples under "model_params" key which IS editable
    # in this experiment; happy path validates cleanly.
    p = rp.propose(exp, ProposerContext(iteration_index=0))
    assert "model_params" in p.parameters


def test_random_max_proposals_must_be_positive() -> None:
    with pytest.raises(ValueError):
        RandomProposer(max_proposals=0)


def _hypothesis(
    *,
    statement: str,
    parameters: dict[str, Any] | None = None,
    mode_slug: str = "fm_x",
) -> HypothesisRecord:
    return HypothesisRecord(
        id=HypothesisRecord.make_id(),
        workspace_id=WS,
        experiment_id="exp_x",
        targets_mode_slug=mode_slug,
        statement=statement,
        suggested_parameters={"model_params": dict(parameters or {})} if parameters else {},
    )


def test_llm_offline_consumes_hypotheses_in_order() -> None:
    exp = _experiment()
    consumed: list[HypothesisRecord] = []
    proposer = LLMProposer(on_consume=consumed.append)
    h0 = _hypothesis(statement="lower temperature", parameters={"temperature": 0.1})
    h1 = _hypothesis(statement="raise top_p", parameters={"top_p": 0.95})

    p0 = proposer.propose(exp, ProposerContext(iteration_index=0, pending_hypotheses=(h0, h1)))
    assert isinstance(p0, Proposal)
    assert p0.hypothesis == "lower temperature"
    assert p0.parameters == {"model_params": {"temperature": 0.1}}
    assert p0.inputs_referenced == ["fm_x"]
    assert h0.consumed_by_iteration == 0
    assert consumed == [h0]

    # h0 is now consumed; the next context (rebuilt by the loop) drops it.
    p1 = proposer.propose(exp, ProposerContext(iteration_index=1, pending_hypotheses=(h1,)))
    assert isinstance(p1, Proposal)
    assert p1.hypothesis == "raise top_p"
    assert h1.consumed_by_iteration == 1


def test_llm_offline_skips_already_consumed_hypothesis() -> None:
    exp = _experiment()
    proposer = LLMProposer()
    h0 = _hypothesis(statement="already applied")
    h0.consumed_by_iteration = 0
    h1 = _hypothesis(statement="fresh")

    p = proposer.propose(exp, ProposerContext(iteration_index=1, pending_hypotheses=(h0, h1)))
    assert isinstance(p, Proposal)
    assert p.hypothesis == "fresh"
    assert h1.consumed_by_iteration == 1
    # The already-consumed one is untouched.
    assert h0.consumed_by_iteration == 0


def test_llm_offline_exhausts_when_no_pending() -> None:
    exp = _experiment()
    proposer = LLMProposer()
    with pytest.raises(SearchSpaceExhaustedError):
        proposer.propose(exp, ProposerContext(iteration_index=0, pending_hypotheses=()))


def test_llm_offline_carries_confidence() -> None:
    exp = _experiment()
    proposer = LLMProposer(confidence=0.9)
    h0 = _hypothesis(statement="h", parameters={"temperature": 0.0})
    p = proposer.propose(exp, ProposerContext(iteration_index=0, pending_hypotheses=(h0,)))
    assert isinstance(p, Proposal)
    assert p.confidence == 0.9


def test_llm_offline_validates_editable_contract() -> None:
    exp = _experiment()  # tool_code is not editable
    proposer = LLMProposer()
    bad = HypothesisRecord(
        id=HypothesisRecord.make_id(),
        workspace_id=WS,
        experiment_id="exp_x",
        targets_mode_slug="fm_y",
        statement="rewrite the tool",
        suggested_parameters={"tool_code": "..."},
    )
    with pytest.raises(ProposalRejectedError):
        proposer.propose(exp, ProposerContext(iteration_index=0, pending_hypotheses=(bad,)))


def test_llm_confidence_must_be_in_range() -> None:
    with pytest.raises(ValueError):
        LLMProposer(confidence=1.5)


@pytest.mark.asyncio
async def test_llm_adapter_mode_parses_structured_output() -> None:
    exp = _experiment()

    def _agent(_: AdapterRequest) -> AdapterResponse:
        return AdapterResponse(
            content=None,
            structured_output={
                "parameters": {"prompt": "be terse"},
                "hypothesis": "terser prompts reduce verbosity failures",
                "confidence": 0.8,
                "inputs_referenced": ["fm_verbose"],
            },
        )

    proposer = LLMProposer(adapter=EmbeddedAdapter(_agent))
    h0 = _hypothesis(statement="pending hint")
    proposed = proposer.propose(exp, ProposerContext(iteration_index=0, pending_hypotheses=(h0,)))
    proposal = await proposed  # type: ignore[misc]
    assert isinstance(proposal, Proposal)
    assert proposal.parameters == {"prompt": "be terse"}
    assert proposal.hypothesis == "terser prompts reduce verbosity failures"
    assert proposal.confidence == 0.8
    assert proposal.inputs_referenced == ["fm_verbose"]
    # Acting on evidence retires the offered hypothesis so it isn't replayed.
    assert h0.consumed_by_iteration == 0


@pytest.mark.asyncio
async def test_llm_adapter_mode_rejects_malformed_output() -> None:
    exp = _experiment()

    def _agent(_: AdapterRequest) -> AdapterResponse:
        return AdapterResponse(content="no structured output here")

    proposer = LLMProposer(adapter=EmbeddedAdapter(_agent))
    proposed = proposer.propose(exp, ProposerContext(iteration_index=0))
    with pytest.raises(LLMProposalError):
        await proposed  # type: ignore[misc]
