from __future__ import annotations

import pytest
from pydantic import ValidationError

from selfevals.schemas.enums import FeatureKind, FeatureStatus
from selfevals.schemas.registry import (
    FeatureRegistry,
    RiskDimension,
    RiskProfile,
    RiskRegistry,
)

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _feature(**overrides: object) -> FeatureRegistry:
    base: dict[str, object] = {
        "id": FeatureRegistry.make_id(),
        "workspace_id": WS,
        "kind": FeatureKind.PRODUCT_FEATURE,
        "primary_feature": "commerce.product_resolution",
        "description": "Resolve a customer-mentioned product to a catalog SKU.",
        "default_risk": RiskProfile(overall="medium"),
    }
    base.update(overrides)
    return FeatureRegistry(**base)  # type: ignore[arg-type]


def test_feature_happy_path() -> None:
    f = _feature()
    assert f.status == FeatureStatus.PROPOSED
    assert f.failure_weight_defaults == {}


@pytest.mark.parametrize(
    "bad",
    [
        "Commerce.product",
        "commerce..product",
        ".commerce",
        "commerce.",
        "1commerce",
        "commerce.product-resolution",  # hyphen not allowed
        "",
    ],
)
def test_invalid_feature_path_rejected(bad: str) -> None:
    with pytest.raises(ValidationError):
        _feature(primary_feature=bad)


@pytest.mark.parametrize(
    "good",
    [
        "commerce",
        "commerce.product_resolution",
        "support.escalation.tier_2",
        "safety.policy_compliance",
    ],
)
def test_valid_feature_paths(good: str) -> None:
    f = _feature(primary_feature=good)
    assert f.primary_feature == good


def test_failure_weights_must_be_non_negative() -> None:
    with pytest.raises(ValidationError):
        _feature(failure_weight_defaults={"wrong_product": -1})


def test_failure_weight_keys_validated() -> None:
    with pytest.raises(ValidationError):
        _feature(failure_weight_defaults={"bad key!": 1})


def test_risk_registry_unique_dimensions() -> None:
    with pytest.raises(ValidationError):
        RiskRegistry(
            id=RiskRegistry.make_id(),
            workspace_id=WS,
            dimensions=[
                RiskDimension(name="overall", levels=["low", "high"]),
                RiskDimension(name="overall", levels=["a", "b"]),
            ],
        )


def test_risk_registry_dimension_lookup() -> None:
    rr = RiskRegistry(
        id=RiskRegistry.make_id(),
        workspace_id=WS,
        dimensions=[
            RiskDimension(name="overall", levels=["low", "medium", "high", "critical"]),
            RiskDimension(
                name="reversibility", levels=["reversible", "needs_approval", "irreversible"]
            ),
        ],
    )
    assert rr.has_dimension("overall")
    assert not rr.has_dimension("foo")
    assert rr.levels_for("overall") == ["low", "medium", "high", "critical"]
    with pytest.raises(KeyError):
        rr.levels_for("missing")


def test_risk_dimension_unique_levels() -> None:
    with pytest.raises(ValidationError):
        RiskDimension(name="overall", levels=["low", "low"])


def test_risk_dimension_min_two_levels() -> None:
    with pytest.raises(ValidationError):
        RiskDimension(name="overall", levels=["low"])
