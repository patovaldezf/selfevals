"""`search_space.agents` — the binding axis.

Payload axes (`model_params`) ride inside a request to one fixed agent. The
agent axis changes *which adapter is built*, which is decided before the first
request — so these tests assert that a proposal actually reaches a different
agent, not just that a different dict was passed along.
"""

from __future__ import annotations

import pytest

from selfevals.optimization.proposers import (
    GridProposer,
    ProposerContext,
    RandomProposer,
    SearchSpaceExhaustedError,
    describe_agent,
)
from selfevals.schemas._base import EntityRef
from selfevals.schemas.enums import DatasetType, Mode, ProposerStrategy, SandboxMode
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
from selfevals.schemas.iteration import ProposalRejectedError

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"

_AGENT_A = {"type": "http", "url": "https://a.example/run", "name": "provider-a"}
_AGENT_B = {"type": "http", "url": "https://b.example/run", "name": "provider-b"}


def _experiment(
    *,
    agents: list[dict[str, object]] | None = None,
    model_params: dict[str, object] | None = None,
    model_choice: bool = True,
) -> Experiment:
    return Experiment(
        id=Experiment.make_id(),
        workspace_id=WS,
        name="binding axis",
        goal="compare providers",
        mode=Mode.HANDOFF,
        taxonomy=ExperimentTaxonomy(
            target_features=["commerce.product_resolution"],
            dataset_types=[DatasetType.CAPABILITY],
        ),
        datasets=DatasetUsage(optimization=EntityRef(id="ds_x", version=1)),
        target=TargetSpec(primary=MetricTarget(name="pass@1", operator=">=", value=0.85)),
        editable=EditableContract(model_choice=model_choice),
        frozen=FrozenSnapshot(
            fleet=EntityRef(id="flt_x"),
            agents=[EntityRef(id="ag_x")],
            datasets=[EntityRef(id="ds_y")],
        ),
        proposer=ProposerSpec(strategy=ProposerStrategy.GRID),
        run=RunSpec(sandbox=SandboxMode.DRY_RUN),
        search_space=SearchSpace(
            model_params=model_params or {},
            agents=agents or [],
        ),
    )


def _ctx(i: int) -> ProposerContext:
    return ProposerContext(iteration_index=i)


def test_grid_over_agents_alone_is_one_iteration_per_agent() -> None:
    """The pure bake-off: no params, N agents, N iterations."""
    exp = _experiment(agents=[_AGENT_A, _AGENT_B])
    proposer = GridProposer()

    first = proposer.propose(exp, _ctx(0))
    second = proposer.propose(exp, _ctx(1))

    assert first.parameters["agent"] == _AGENT_A
    assert second.parameters["agent"] == _AGENT_B
    with pytest.raises(SearchSpaceExhaustedError):
        proposer.propose(exp, _ctx(2))


def test_grid_multiplies_agents_by_model_params() -> None:
    """2 agents x 2 temperatures = 4 iterations, agent varying slowest."""
    exp = _experiment(agents=[_AGENT_A, _AGENT_B], model_params={"temperature": [0.0, 1.0]})
    proposer = GridProposer()

    combos = [proposer.propose(exp, _ctx(i)) for i in range(4)]

    assert [c.parameters["agent"]["name"] for c in combos] == [
        "provider-a",
        "provider-a",
        "provider-b",
        "provider-b",
    ]
    assert [c.parameters["model_params"]["temperature"] for c in combos] == [0.0, 1.0, 0.0, 1.0]
    with pytest.raises(SearchSpaceExhaustedError):
        proposer.propose(exp, _ctx(4))


def test_grid_without_either_axis_still_raises() -> None:
    """An empty search space is a spec error, not an empty sweep."""
    with pytest.raises(ValueError, match="search_space"):
        GridProposer().propose(_experiment(), _ctx(0))


def test_agent_proposal_is_gated_by_model_choice() -> None:
    """Swapping the agent is the strongest form of choosing a model.

    An experiment that forbids `model_choice` must not get a different provider
    through the search space's back door.
    """
    exp = _experiment(agents=[_AGENT_A], model_choice=False)
    with pytest.raises(ProposalRejectedError):
        GridProposer().propose(exp, _ctx(0))


def test_random_samples_an_agent() -> None:
    exp = _experiment(agents=[_AGENT_A, _AGENT_B], model_params={"temperature": [0.0, 1.0]})
    proposal = RandomProposer(max_proposals=5, seed=7).propose(exp, _ctx(0))

    assert proposal.parameters["agent"] in (_AGENT_A, _AGENT_B)
    assert proposal.parameters["model_params"]["temperature"] in (0.0, 1.0)


def test_hypothesis_names_the_agent_not_its_raw_dict() -> None:
    """A raw agent mapping in the hypothesis is unreadable in a report."""
    exp = _experiment(agents=[_AGENT_A])
    proposal = GridProposer().propose(exp, _ctx(0))

    assert "provider-a" in proposal.hypothesis
    assert "https://a.example" not in proposal.hypothesis


@pytest.mark.parametrize(
    ("block", "expected"),
    [
        ({"name": "explicit"}, "explicit"),
        ({"type": "http", "url": "https://x/y"}, "http:https://x/y"),
        ({"type": "embedded", "entrypoint": "pkg.mod:fn"}, "embedded:pkg.mod:fn"),
        ({"type": "cli", "command": ["python", "agent.py"]}, "cli:python agent.py"),
        ({"type": "http"}, "http"),
    ],
)
def test_describe_agent_labels_each_transport(block: dict[str, object], expected: str) -> None:
    assert describe_agent(block) == expected
