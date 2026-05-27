"""Closed enums reject unknown values."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from selfevals.schemas import enums


@pytest.mark.parametrize(
    ("enum_cls", "valid", "invalid"),
    [
        (enums.Role, "viewer", "owner"),
        (enums.Level, "tool_call", "bag_of_calls"),
        (enums.DatasetSource, "production", "prod"),
        (enums.GroundTruthMethod, "rubric", "rubric_v2"),
        (enums.DatasetType, "regression", "regress"),
        (enums.SandboxMode, "mock", "fake"),
        (enums.RuntimeLocation, "canary", "preview"),
        (enums.Mode, "agent_loop", "loop"),
        (enums.ProposerStrategy, "manual", "by_hand"),
        (enums.ExperimentState, "running", "in_flight"),
        (enums.SpanKind, "llm_call", "llm"),
        (enums.StopReason, "end_turn", "natural"),
        (enums.TraceState, "completed", "done"),
        (enums.ToolCallStatus, "ok", "success"),
        (enums.PIIStatus, "scrubbed", "clean"),
        (enums.FeatureKind, "product_feature", "feature"),
        (enums.FeatureStatus, "active", "live"),
        (enums.AgentType, "system_prompt", "prompt"),
        (enums.AgentStatus, "production", "prod"),
        (enums.FleetStatus, "active", "live"),
        (enums.DatasetStatus, "frozen", "locked"),
        (enums.ToolStatus, "active", "live"),
        (enums.GraderCardState, "calibrated", "trained"),
        (enums.DecisionOutcome, "keep_candidate", "keep"),
        (enums.IterationState, "completed", "ok"),
        (enums.Modality, "text", "string"),
    ],
)
def test_enum_accepts_valid_rejects_invalid(enum_cls: type, valid: str, invalid: str) -> None:
    class M(BaseModel):
        v: enum_cls  # type: ignore[valid-type]

    assert M(v=valid).v == valid
    with pytest.raises(ValidationError):
        M(v=invalid)


def test_role_canonical_set() -> None:
    assert set(enums.Role) == {
        enums.Role.VIEWER,
        enums.Role.EVALUATOR,
        enums.Role.EXPERIMENTER,
        enums.Role.MAINTAINER,
        enums.Role.ADMIN,
        enums.Role.AUDITOR,
    }
