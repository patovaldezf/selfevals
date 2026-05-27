"""ErrorAnalysisSpec: the YAML opt-in is declarative and its trigger predicate
fires only when enabled AND the fail rate clears the threshold (design §9)."""

from __future__ import annotations

import pytest

from selfevals.schemas.experiment import AnalysisTriggerSpec, ErrorAnalysisSpec


def test_disabled_never_stages() -> None:
    spec = ErrorAnalysisSpec(enabled=False, trigger=AnalysisTriggerSpec(threshold=0.0))
    # Even a total wipeout does not stage when the block is off.
    assert spec.should_stage(fail_rate=1.0) is False


def test_enabled_stages_only_above_threshold() -> None:
    spec = ErrorAnalysisSpec(enabled=True, trigger=AnalysisTriggerSpec(threshold=0.10))
    assert spec.should_stage(fail_rate=0.05) is False
    # The threshold is strict: equal is not "above".
    assert spec.should_stage(fail_rate=0.10) is False
    assert spec.should_stage(fail_rate=0.11) is True


def test_defaults_are_off_and_failed_only() -> None:
    spec = ErrorAnalysisSpec()
    assert spec.enabled is False
    assert spec.scope == "failed_only"
    assert spec.taxonomy == "workspace"
    assert spec.trigger.when == "fail_rate_above"
    assert spec.should_stage(fail_rate=1.0) is False


def test_threshold_is_bounded() -> None:
    with pytest.raises(ValueError):
        AnalysisTriggerSpec(threshold=1.5)
    with pytest.raises(ValueError):
        AnalysisTriggerSpec(threshold=-0.1)


def test_round_trips_through_json() -> None:
    spec = ErrorAnalysisSpec(
        enabled=True, scope="all", trigger=AnalysisTriggerSpec(threshold=0.25)
    )
    restored = ErrorAnalysisSpec.model_validate_json(spec.model_dump_json())
    assert restored == spec
