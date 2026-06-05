"""Shared `Experiment` builder for API tests.

Constructing a valid `Experiment` is verbose (many required nested specs), so
the API tests that seed experiments directly share one factory rather than each
re-deriving the boilerplate.
"""

from __future__ import annotations

from datetime import UTC, datetime

from selfevals.schemas._base import EntityRef
from selfevals.schemas.enums import (
    DatasetType,
    ExperimentState,
    Mode,
    ProposerStrategy,
    SandboxMode,
)
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

_TS = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)


def make_experiment(
    *,
    workspace_id: str,
    id: str | None = None,
    name: str = "exp",
    state: ExperimentState = ExperimentState.DRAFT,
    features: list[str] | None = None,
) -> Experiment:
    """Build a valid `Experiment` with sensible defaults for seeding.

    `state` is set directly (bypassing the transition machine) because fixtures
    need to pin an arbitrary lifecycle state.
    """
    exp = Experiment(
        id=id or Experiment.make_id(),
        workspace_id=workspace_id,
        created_at=_TS,
        updated_at=_TS,
        name=name,
        goal="g",
        mode=Mode.HANDOFF,
        taxonomy=ExperimentTaxonomy(
            target_features=features or ["commerce.product_resolution"],
            dataset_types=[DatasetType.CAPABILITY],
        ),
        datasets=DatasetUsage(optimization=EntityRef(id="ds_x", version=1)),
        target=TargetSpec(primary=MetricTarget(name="pass@1", operator=">=", value=0.85)),
        editable=EditableContract(prompt=True, model_params=True),
        frozen=FrozenSnapshot(
            fleet=EntityRef(id="flt_x"),
            agents=[EntityRef(id="ag_x")],
            datasets=[EntityRef(id="ds_y")],
        ),
        proposer=ProposerSpec(strategy=ProposerStrategy.GRID),
        run=RunSpec(sandbox=SandboxMode.DRY_RUN),
        search_space=SearchSpace(),
    )
    exp.state = state
    return exp
