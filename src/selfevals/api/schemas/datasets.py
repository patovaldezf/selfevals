"""Dataset CRUD, case append, baseline/regression, and failure-mode taxonomy shapes."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from selfevals.api.schemas.traces import CaseSummary


class SplitAllocationView(BaseModel):
    """A dataset's optimization/holdout/reliability split fractions."""

    optimization: float
    holdout: float
    reliability: float
    other: dict[str, float] = Field(default_factory=dict)


class DatasetStatisticsView(BaseModel):
    """Portfolio counts for a dataset's cases (computed at materialization)."""

    total_cases: int
    by_level: dict[str, int] = Field(default_factory=dict)
    by_feature: dict[str, int] = Field(default_factory=dict)
    by_source: dict[str, int] = Field(default_factory=dict)
    by_risk: dict[str, int] = Field(default_factory=dict)
    holdout_count: int = 0
    pii_breakdown: dict[str, int] = Field(default_factory=dict)


class DatasetSummary(BaseModel):
    """A dataset as it appears in a list — identity + shape, no case bodies."""

    id: str
    name: str
    description: str | None = None
    dataset_type: str
    status: str
    case_count: int
    manifest_hash: str | None = None
    created_at: datetime
    updated_at: datetime


class DatasetListPage(BaseModel):
    """Paginated `GET /workspaces/{ws}/datasets`."""

    items: list[DatasetSummary] = Field(default_factory=list)
    total: int
    limit: int
    offset: int
    has_more: bool


class DatasetDetailResponse(BaseModel):
    """One dataset with its split, statistics, and resolved case summaries.

    `cases` reuses `CaseSummary` — the same shape the experiment cases list
    returns — so the FE renders a dataset's cases with one component.
    """

    id: str
    name: str
    description: str | None = None
    dataset_type: str
    status: str
    case_count: int
    manifest_hash: str | None = None
    split_allocation: SplitAllocationView
    statistics: DatasetStatisticsView | None = None
    cases: list[CaseSummary] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class CreateDatasetRequest(BaseModel):
    """Create a dataset over HTTP (JSON body). Provide exactly one case source:

    * `cases` — the case dicts inline in the request body.
    * `cases_path` — a path to a JSONL file already on the server's disk.

    (To upload a `.jsonl` file directly, use the multipart `…/datasets/upload`
    endpoint instead.) `dataset_type` defaults to `capability`; an optional
    `split_allocation` overrides the default 0.7/0.2/0.1 portfolio.
    """

    name: str = Field(min_length=1)
    description: str | None = None
    dataset_type: str = "capability"
    cases: list[dict[str, Any]] | None = None
    cases_path: str | None = None
    split_allocation: dict[str, float] | None = None

    @model_validator(mode="after")
    def _exactly_one_source(self) -> CreateDatasetRequest:
        if (self.cases is None) == (self.cases_path is None):
            raise ValueError("provide exactly one of `cases` or `cases_path`")
        return self


class AppendDatasetCaseRequest(BaseModel):
    """Append one validated EvalCase dict to a dataset.

    Frozen regression datasets are immutable. When `create_version_if_frozen`
    is true, the backend creates a new active dataset version instead of
    mutating the frozen manifest.
    """

    case: dict[str, Any]
    create_version_if_frozen: bool = True


class AppendDatasetCaseResponse(BaseModel):
    dataset: DatasetDetailResponse
    case_id: str
    created_new_dataset: bool = False


# --- Failure-mode taxonomy (loop-closer) --------------------------------
# View + request shapes for the taxonomy UI. The domain logic lives in
# `cli/analyze_commands.py`; these expose it over HTTP. A mode's status is the
# promotion gate (candidate → official → retired); `example_count` keeps the
# list cheap (no full example bodies until the detail view needs them).


class FailureModeResponse(BaseModel):
    """One failure mode, projected for the taxonomy UI."""

    id: str
    slug: str
    title: str
    definition: str
    status: str
    parent_mode_id: str | None = None
    proposed_by: str
    example_count: int = 0
    first_seen_iteration: int | None = None
    superseded_by: str | None = None
    created_at: datetime
    updated_at: datetime


class FailureModeListResponse(BaseModel):
    items: list[FailureModeResponse] = Field(default_factory=list)


class MergeFailureModeRequest(BaseModel):
    """Merge this mode's examples into `into_id`, then retire the source."""

    into_id: str = Field(min_length=1)


class EditFailureModeRequest(BaseModel):
    """Patch a mode's human-facing text. At least one field must be set."""

    title: str | None = None
    definition: str | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> EditFailureModeRequest:
        if self.title is None and self.definition is None:
            raise ValueError("provide at least one of `title` or `definition`")
        return self


# --- Baseline & regression (loop-closer) --------------------------------
# Exposes `runner/baseline.py` + `ci/regression.py` over HTTP, anchored to a
# dataset. The FE shows the current baseline on the dataset/iteration views and
# runs a regression check against it.


class BaselineResponse(BaseModel):
    dataset_id: str
    iteration_id: str
    experiment_id: str | None = None
    primary_metric_name: str
    primary_metric_value: float
    error_rate: float | None = None
    created_at: datetime


class SetBaselineRequest(BaseModel):
    """Re-anchor a dataset's baseline. `iteration_id` omitted = use the best
    completed iteration on the dataset (same default as the CLI)."""

    iteration_id: str | None = None


class RegressionCheckRequest(BaseModel):
    iteration_id: str = Field(min_length=1)
    primary_drop: float = 0.0
    per_class_f1_drop: float = 0.05
    error_rate_rise: float = 0.0


class RegressionFindingResponse(BaseModel):
    """Mirrors `ci.regression.RegressionFinding`. `regressed=True` means this
    signal failed the gate; a populated `detail` with `regressed=False` is an
    informational note (improvement / class appeared)."""

    signal: str
    baseline: float | None = None
    current: float | None = None
    delta: float | None = None
    regressed: bool
    detail: str


class RegressionResultResponse(BaseModel):
    dataset_id: str
    iteration_id: str
    regressed: bool
    findings: list[RegressionFindingResponse] = Field(default_factory=list)


# --- Error-analysis bundle / ingest (loop-closer) -----------------------
# Thin HTTP envelopes around `analysis/bundle.py` + `analysis/ingest.py`. The
# bundle/result bodies themselves are the domain Pydantic models from
# `analysis/schemas.py`, passed through as opaque JSON so the contract stays
# defined in one place.


class AnalysisIngestSummaryResponse(BaseModel):
    assignments_applied: int
    created_candidates: list[str] = Field(default_factory=list)
    updated_candidates: list[str] = Field(default_factory=list)
    hypotheses_recorded: int
