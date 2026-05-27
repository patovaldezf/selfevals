from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from selfeval.schemas.enums import (
    DatasetSource,
    DatasetType,
    GroundTruthMethod,
    Level,
    PIIStatus,
    RuntimeLocation,
)
from selfeval.schemas.eval_case import (
    CaseMetadata,
    CaseTaxonomy,
    EvalCase,
    Expected,
    FeatureTag,
    GroundTruthSpec,
    SourceInfo,
)

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _taxonomy(
    *,
    source: DatasetSource = DatasetSource.HANDCRAFTED,
    dataset_type: DatasetType = DatasetType.CAPABILITY,
    level: Level = Level.FINAL_RESPONSE,
) -> CaseTaxonomy:
    return CaseTaxonomy(
        level=level,
        feature=FeatureTag(primary="commerce.product_resolution"),
        source=SourceInfo(type=source),
        ground_truth=GroundTruthSpec(methods=[GroundTruthMethod.EXACT_MATCH]),
        runtime=RuntimeLocation.OFFLINE,
        dataset_type=dataset_type,
    )


def _case(**overrides: object) -> EvalCase:
    base: dict[str, Any] = {
        "id": EvalCase.make_id(),
        "workspace_id": WS,
        "name": "resolves manzanas to product",
        "task_type": "product_resolution",
        "input": {"messages": [{"role": "user", "content": "necesito manzanas rojas"}]},
        "taxonomy": _taxonomy(),
    }
    base.update(overrides)
    return EvalCase(**base)


def test_case_happy() -> None:
    c = _case()
    assert c.metadata.pii_status == PIIStatus.RAW
    assert c.holdout is False
    assert c.modalities[0] == "text"


def test_production_raw_pii_requires_approval() -> None:
    with pytest.raises(ValidationError):
        _case(taxonomy=_taxonomy(source=DatasetSource.PRODUCTION))


def test_production_raw_pii_with_approval_ok() -> None:
    c = _case(
        taxonomy=_taxonomy(source=DatasetSource.PRODUCTION),
        metadata=CaseMetadata(
            pii_status=PIIStatus.RAW,
            approved_raw_by="patricio",
            approved_raw_at=datetime(2026, 5, 16, tzinfo=UTC),
        ),
    )
    assert c.metadata.pii_status == PIIStatus.RAW


def test_production_scrubbed_pii_ok_without_approval() -> None:
    c = _case(
        taxonomy=_taxonomy(source=DatasetSource.PRODUCTION),
        metadata=CaseMetadata(pii_status=PIIStatus.SCRUBBED),
    )
    assert c.metadata.pii_status == PIIStatus.SCRUBBED


def test_primary_cannot_be_in_secondary() -> None:
    with pytest.raises(ValidationError):
        FeatureTag(primary="x.y", secondary=["x.y"])


def test_required_and_forbidden_tools_must_be_disjoint() -> None:
    with pytest.raises(ValidationError):
        _case(
            expected=Expected(
                required_tools=["search"],
                forbidden_tools=["search"],
            )
        )


def test_dataset_type_singular() -> None:
    # Schema-level: assignment must be a single DatasetType value.
    with pytest.raises(ValidationError):
        _case(taxonomy={**_taxonomy().model_dump(), "dataset_type": ["regression", "capability"]})


def test_failure_weights_non_negative() -> None:
    with pytest.raises(ValidationError):
        _case(failure_weights={"wrong_product": -3})


def test_modalities_must_be_unique_and_nonempty() -> None:
    with pytest.raises(ValidationError):
        _case(modalities=["text", "text"])
    with pytest.raises(ValidationError):
        _case(modalities=[])


def test_graders_unique() -> None:
    with pytest.raises(ValidationError):
        _case(graders=["g_a", "g_a"])


def test_ground_truth_methods_dedup() -> None:
    with pytest.raises(ValidationError):
        GroundTruthSpec(methods=[GroundTruthMethod.EXACT_MATCH, GroundTruthMethod.EXACT_MATCH])
