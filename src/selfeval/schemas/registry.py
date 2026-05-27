"""Declarative registries: features and risk taxonomy.

A `FeatureRegistry` row defines a feature path (e.g. `commerce.product_resolution`)
along with default risk and failure weights. Cases tag themselves with primary +
secondary feature paths; the registry is the source of truth for what is legal.

A `RiskRegistry` row declares the dimensions and allowed levels in this workspace
(e.g. overall: [low, medium, high, critical]). It is opaque to selfeval core
beyond enforcement that case `risk` payloads reference declared dimensions.
"""

from __future__ import annotations

import re
from typing import ClassVar

from pydantic import Field, field_validator, model_validator

from selfeval.schemas._base import BaseEntity, NonEmptyStr, SelfEvalModel
from selfeval.schemas.enums import FeatureKind, FeatureStatus

_FEATURE_PATH_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")


def _validate_feature_path(value: str) -> str:
    if not _FEATURE_PATH_RE.match(value):
        raise ValueError(
            f"feature path {value!r} must be dotted lowercase identifiers "
            "(e.g. 'commerce.product_resolution')"
        )
    return value


class RiskProfile(SelfEvalModel):
    """Per-dimension risk levels. Dimensions are workspace-defined via RiskRegistry."""

    overall: NonEmptyStr
    user_trust: NonEmptyStr | None = None
    privacy: NonEmptyStr | None = None
    reversibility: NonEmptyStr | None = None
    safety: NonEmptyStr | None = None
    cost: NonEmptyStr | None = None


class FeatureRegistry(BaseEntity):
    """Declares one feature path the workspace knows about."""

    _id_prefix: ClassVar[str] = "ftr"

    kind: FeatureKind
    primary_feature: NonEmptyStr
    owner: NonEmptyStr | None = None
    description: NonEmptyStr
    default_risk: RiskProfile
    failure_weight_defaults: dict[str, int] = Field(default_factory=dict)
    parameters: dict[str, object] | None = None
    status: FeatureStatus = FeatureStatus.PROPOSED
    replacement_feature_id: str | None = None

    @field_validator("primary_feature")
    @classmethod
    def _check_path(cls, value: str) -> str:
        return _validate_feature_path(value)

    @field_validator("failure_weight_defaults")
    @classmethod
    def _check_weights(cls, value: dict[str, int]) -> dict[str, int]:
        for key, weight in value.items():
            if not key or not key.replace("_", "").isalnum():
                raise ValueError(f"invalid failure weight key: {key!r}")
            if weight < 0:
                raise ValueError(f"failure weight for {key!r} must be >= 0")
        return value

    @model_validator(mode="after")
    def _deprecated_implies_replacement_or_none(self) -> FeatureRegistry:
        if self.status == FeatureStatus.REMOVED and self.replacement_feature_id is None:
            # Removed features may still exist for audit; no replacement required,
            # but they cannot be referenced by new cases — that constraint lives
            # in EvalCase validation, not here.
            pass
        return self


class RiskDimension(SelfEvalModel):
    """One named dimension with its allowed levels (ordered low → high)."""

    name: NonEmptyStr
    levels: list[NonEmptyStr] = Field(min_length=2)

    @field_validator("levels")
    @classmethod
    def _unique_levels(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("risk levels must be unique within a dimension")
        return value


class RiskRegistry(BaseEntity):
    """Workspace-wide risk taxonomy.

    `dimensions` is the canonical set; `RiskProfile` values on cases/features
    must reference one of these dimensions with a level from its declared set.
    Enforcement of that cross-reference lives in EvalCase validation.
    """

    _id_prefix: ClassVar[str] = "rsk"

    dimensions: list[RiskDimension] = Field(min_length=1)

    @model_validator(mode="after")
    def _no_duplicate_dimensions(self) -> RiskRegistry:
        names = [d.name for d in self.dimensions]
        if len(set(names)) != len(names):
            raise ValueError("dimension names must be unique within a RiskRegistry")
        return self

    def has_dimension(self, name: str) -> bool:
        return any(d.name == name for d in self.dimensions)

    def levels_for(self, dimension: str) -> list[str]:
        for d in self.dimensions:
            if d.name == dimension:
                return d.levels
        raise KeyError(dimension)
