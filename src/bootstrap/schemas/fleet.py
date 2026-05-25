"""AgentFleet and Agent: the things experiments run.

An `Agent` is one configurable unit (system prompt, graph, or handoff target).
Long-form fields (`system_prompt`, `graph_definition`) live in object storage
and are referenced by `*_pointer` + recorded by `content_hash` for replay.

An `AgentFleet` is a snapshot of which agents/tools/features are bound for a
given Experiment. Once frozen, an Experiment locks the fleet version.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import Field, field_validator, model_validator

from bootstrap.schemas._base import BaseEntity, BootstrapModel, EntityRef, NonEmptyStr
from bootstrap.schemas.enums import AgentStatus, AgentType, FleetStatus, Modality


class ModelRef(BootstrapModel):
    """Reference to a model deployment."""

    provider: NonEmptyStr
    name: NonEmptyStr
    """The model identifier (e.g. 'claude-sonnet-4-6', 'gpt-5'). Pinned at
    runtime via `model_version_pinned` on the LLM span."""


class Agent(BaseEntity):
    _id_prefix: ClassVar[str] = "ag"

    fleet_id: NonEmptyStr | None = None
    """Set once the agent is bound into a fleet snapshot; None for orphans."""

    agent_type: AgentType
    model: ModelRef
    system_prompt_pointer: str | None = None
    """Pointer into object store. None for graph/handoff agents."""

    graph_definition_pointer: str | None = None
    """Pointer to YAML/JSON graph definition. None for prompt/handoff agents."""

    handoff_target_id: str | None = None
    """For agent_type=handoff: the external system/queue identifier."""

    tools: list[NonEmptyStr] = Field(default_factory=list)
    features: list[NonEmptyStr] = Field(default_factory=list)
    parameters: dict[str, object] = Field(default_factory=dict)
    modalities: list[Modality] = Field(default_factory=lambda: [Modality.TEXT])
    content_hash: str | None = None
    status: AgentStatus = AgentStatus.DRAFT

    @model_validator(mode="after")
    def _type_specific_payload(self) -> Agent:
        match self.agent_type:
            case AgentType.SYSTEM_PROMPT:
                if self.system_prompt_pointer is None:
                    raise ValueError("agent_type=system_prompt requires system_prompt_pointer")
                if self.graph_definition_pointer is not None:
                    raise ValueError(
                        "agent_type=system_prompt cannot define graph_definition_pointer"
                    )
            case AgentType.GRAPH:
                if self.graph_definition_pointer is None:
                    raise ValueError("agent_type=graph requires graph_definition_pointer")
            case AgentType.HANDOFF:
                if self.handoff_target_id is None:
                    raise ValueError("agent_type=handoff requires handoff_target_id")
        return self

    @field_validator("tools", "features")
    @classmethod
    def _unique_refs(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("duplicate entries are not allowed")
        return value


class AgentFleet(BaseEntity):
    """Snapshot of agents + tools + features bound for an Experiment."""

    _id_prefix: ClassVar[str] = "flt"

    name: NonEmptyStr
    description: str | None = None
    agents: list[EntityRef] = Field(min_length=1)
    tools: list[EntityRef] = Field(default_factory=list)
    features: list[NonEmptyStr] = Field(default_factory=list)
    """Feature paths covered by this fleet — must exist in FeatureRegistry."""

    feature_params: dict[str, dict[str, object]] = Field(default_factory=dict)
    """Per-feature parameter overrides keyed by feature path."""

    content_hash: str | None = None
    status: FleetStatus = FleetStatus.DRAFT

    @model_validator(mode="after")
    def _no_duplicate_agents_or_tools(self) -> AgentFleet:
        agent_ids = [a.id for a in self.agents]
        if len(set(agent_ids)) != len(agent_ids):
            raise ValueError("fleet contains duplicate agent references")
        tool_ids = [t.id for t in self.tools]
        if len(set(tool_ids)) != len(tool_ids):
            raise ValueError("fleet contains duplicate tool references")
        if len(set(self.features)) != len(self.features):
            raise ValueError("fleet contains duplicate feature paths")
        for path in self.feature_params:
            if path not in self.features:
                raise ValueError(f"feature_params references {path!r} not bound in fleet.features")
        return self
