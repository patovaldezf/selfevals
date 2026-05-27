"""Dataset manifest: a typed portfolio of EvalCases.

A Dataset has exactly one `DatasetType` and references its cases by id+version
(immutable refs). Statistics are computed lazily and cached on
`manifest_hash` — recomputing happens when the manifest changes.

Regression-class datasets are immutable in content: once `dataset_type=regression`
and `status=frozen`, attempts to mutate `cases` or `dataset_type` raise.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import Field, field_validator, model_validator

from selfeval.schemas._base import BaseEntity, EntityRef, NonEmptyStr, SelfEvalModel
from selfeval.schemas.enums import DatasetStatus, DatasetType


class SplitAllocation(SelfEvalModel):
    """Fractional allocation across optimization/holdout/reliability/other.

    Fractions sum to 1.0 within `tolerance`; missing splits default to 0.
    """

    optimization: float = Field(default=0.7, ge=0.0, le=1.0)
    holdout: float = Field(default=0.2, ge=0.0, le=1.0)
    reliability: float = Field(default=0.1, ge=0.0, le=1.0)
    other: dict[str, float] = Field(default_factory=dict)

    @field_validator("other")
    @classmethod
    def _other_bounds(cls, value: dict[str, float]) -> dict[str, float]:
        for k, v in value.items():
            if not (0.0 <= v <= 1.0):
                raise ValueError(f"split fraction {k!r} must be in [0, 1]")
        return value

    @model_validator(mode="after")
    def _sums_to_one(self) -> SplitAllocation:
        total = self.optimization + self.holdout + self.reliability + sum(self.other.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"split fractions must sum to 1.0 (got {total:.6f})")
        return self


class DatasetStatistics(SelfEvalModel):
    """Aggregated stats; computed lazily from cases, cached by manifest_hash."""

    total_cases: int = Field(ge=0)
    by_level: dict[str, int] = Field(default_factory=dict)
    by_feature: dict[str, int] = Field(default_factory=dict)
    by_source: dict[str, int] = Field(default_factory=dict)
    by_risk: dict[str, int] = Field(default_factory=dict)
    holdout_count: int = Field(default=0, ge=0)
    pii_breakdown: dict[str, int] = Field(default_factory=dict)


class Dataset(BaseEntity):
    _id_prefix: ClassVar[str] = "ds"

    name: NonEmptyStr
    description: str | None = None
    dataset_type: DatasetType
    cases: list[EntityRef] = Field(default_factory=list)
    split_allocation: SplitAllocation = Field(default_factory=SplitAllocation)
    source_dataset_id: str | None = None
    manifest_hash: str | None = None
    status: DatasetStatus = DatasetStatus.DRAFT
    statistics: DatasetStatistics | None = None
    """Lazy-computed. Storage layer (PR 2) sets/invalidates this; schema layer
    does not auto-compute. Cleared on any mutation of `cases` or `dataset_type`."""

    @field_validator("cases")
    @classmethod
    def _no_duplicate_cases(cls, value: list[EntityRef]) -> list[EntityRef]:
        ids = [c.id for c in value]
        if len(set(ids)) != len(ids):
            raise ValueError("dataset contains duplicate case references")
        return value

    @model_validator(mode="after")
    def _frozen_or_active_requires_manifest_hash(self) -> Dataset:
        if self.status in (DatasetStatus.FROZEN, DatasetStatus.ACTIVE) and not self.manifest_hash:
            raise ValueError(f"Dataset status={self.status} requires a non-empty manifest_hash")
        return self

    def __setattr__(self, name: str, value: object) -> None:
        # Canon §K: regression datasets are content-immutable once frozen.
        # cases and dataset_type cannot mutate after FROZEN. Lifecycle fields
        # (status, version, updated_at, manifest_hash, statistics, deleted_at)
        # remain mutable so storage can roll the dataset forward.
        current_status: DatasetStatus | None = self.__dict__.get("status")
        if (
            current_status == DatasetStatus.FROZEN
            and self.__dict__.get("dataset_type") == DatasetType.REGRESSION
            and name in {"cases", "dataset_type"}
        ):
            raise ValueError(
                f"cannot mutate {name!r}: regression dataset is frozen "
                "(create a new version instead)"
            )
        # Clear statistics cache on case/type mutation in non-frozen datasets.
        if (
            name in {"cases", "dataset_type"}
            and "statistics" in self.__dict__
            and self.__dict__.get("statistics") is not None
        ):
            super().__setattr__("statistics", None)
        super().__setattr__(name, value)
