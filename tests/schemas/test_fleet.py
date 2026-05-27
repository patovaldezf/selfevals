from __future__ import annotations

import pytest
from pydantic import ValidationError

from selfevals.schemas._base import EntityRef
from selfevals.schemas.enums import AgentStatus, AgentType
from selfevals.schemas.fleet import Agent, AgentFleet, ModelRef

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _prompt_agent(**overrides: object) -> Agent:
    base: dict[str, object] = {
        "id": Agent.make_id(),
        "workspace_id": WS,
        "agent_type": AgentType.SYSTEM_PROMPT,
        "model": ModelRef(provider="anthropic", name="claude-sonnet-4-6"),
        "system_prompt_pointer": "oss://prompts/abc",
    }
    base.update(overrides)
    return Agent(**base)  # type: ignore[arg-type]


def test_prompt_agent_happy() -> None:
    a = _prompt_agent()
    assert a.status == AgentStatus.DRAFT
    assert a.agent_type == AgentType.SYSTEM_PROMPT


def test_prompt_agent_requires_pointer() -> None:
    with pytest.raises(ValidationError):
        _prompt_agent(system_prompt_pointer=None)


def test_prompt_agent_rejects_graph_pointer() -> None:
    with pytest.raises(ValidationError):
        _prompt_agent(graph_definition_pointer="oss://graphs/x")


def test_graph_agent_requires_graph_pointer() -> None:
    with pytest.raises(ValidationError):
        Agent(
            id=Agent.make_id(),
            workspace_id=WS,
            agent_type=AgentType.GRAPH,
            model=ModelRef(provider="anthropic", name="claude-sonnet-4-6"),
        )


def test_handoff_agent_requires_target() -> None:
    with pytest.raises(ValidationError):
        Agent(
            id=Agent.make_id(),
            workspace_id=WS,
            agent_type=AgentType.HANDOFF,
            model=ModelRef(provider="anthropic", name="claude-sonnet-4-6"),
        )


def test_agent_tools_features_dedup() -> None:
    with pytest.raises(ValidationError):
        _prompt_agent(tools=["tl_a", "tl_a"])
    with pytest.raises(ValidationError):
        _prompt_agent(features=["x", "x"])


def test_fleet_requires_at_least_one_agent() -> None:
    with pytest.raises(ValidationError):
        AgentFleet(id=AgentFleet.make_id(), workspace_id=WS, name="x", agents=[])


def test_fleet_no_duplicate_agents_or_tools() -> None:
    ref = EntityRef(id="ag_01HZZZZZZZZZZZZZZZZZZZZZZZ")
    with pytest.raises(ValidationError):
        AgentFleet(
            id=AgentFleet.make_id(),
            workspace_id=WS,
            name="x",
            agents=[ref, ref],
        )


def test_fleet_feature_params_must_reference_bound_features() -> None:
    ref = EntityRef(id="ag_01HZZZZZZZZZZZZZZZZZZZZZZZ")
    with pytest.raises(ValidationError):
        AgentFleet(
            id=AgentFleet.make_id(),
            workspace_id=WS,
            name="x",
            agents=[ref],
            features=["commerce.product_resolution"],
            feature_params={"support.unbound": {"k": 1}},
        )


def test_fleet_happy_with_feature_params() -> None:
    ref = EntityRef(id="ag_01HZZZZZZZZZZZZZZZZZZZZZZZ")
    fleet = AgentFleet(
        id=AgentFleet.make_id(),
        workspace_id=WS,
        name="seals-prod",
        agents=[ref],
        features=["commerce.product_resolution", "support.escalation"],
        feature_params={
            "commerce.product_resolution": {"k": 5, "threshold": 0.7},
        },
    )
    assert fleet.feature_params["commerce.product_resolution"]["k"] == 5
