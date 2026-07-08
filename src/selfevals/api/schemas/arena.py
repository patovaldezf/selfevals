"""Arena (code-variant competitions over worktrees).

Thin HTTP envelopes around `arena/service.py`. `spec_template` is the same
dict shape `RunExperimentRequest.spec_inline` accepts, minus the `agent:`
block — Arena injects that per variant at round-launch time.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ArenaBudgetView(BaseModel):
    max_rounds: int | None = None
    max_variants: int | None = None
    max_cost_usd: float | None = None


class CreateArenaRequest(BaseModel):
    name: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    repo_path: str = Field(min_length=1)
    agent_command: list[str] = Field(min_length=1)
    spec_template: dict[str, Any]
    objective_metric: str = Field(min_length=1)
    agent_env: dict[str, str] | None = None
    dataset_id: str | None = None
    budget: ArenaBudgetView | None = None


class ArenaResponse(BaseModel):
    id: str
    workspace_id: str
    name: str
    goal: str
    repo_path: str
    agent_command: list[str]
    objective_metric: str
    dataset_id: str | None = None
    budget: ArenaBudgetView
    current_round: int
    state: str
    winner_variant_id: str | None = None


class RegisterVariantRequest(BaseModel):
    name: str = Field(min_length=1)
    git_ref: str = Field(min_length=1)
    setup_command: list[str] | None = None
    env_overrides: dict[str, str] | None = None
    hypothesis: str | None = None


class ArenaVariantResponse(BaseModel):
    id: str
    arena_id: str
    name: str
    git_ref: str
    resolved_sha: str | None = None
    worktree_path: str | None = None
    hypothesis: str | None = None
    created_in_round: int
    state: str
    error: str | None = None


class LaunchRoundRequest(BaseModel):
    variant_ids: list[str] | None = None
    reps: int = Field(default=1, ge=1)


class RoundEntryView(BaseModel):
    variant_id: str
    experiment_id: str | None = None
    run_job_id: str | None = None
    status: str


class ArenaRoundResponse(BaseModel):
    id: str
    arena_id: str
    index: int
    entries: list[RoundEntryView]
    reps: int
    state: str


class PromoteVariantRequest(BaseModel):
    variant_id: str = Field(min_length=1)


class PromoteVariantResponse(BaseModel):
    winner_variant_id: str
    git_ref: str
    resolved_sha: str | None = None
    suggested_commands: list[str]


class GitRefView(BaseModel):
    name: str
    sha: str


class GitRefsResponse(BaseModel):
    repo_path: str
    refs: list[GitRefView]


class RoundCostEstimateResponse(BaseModel):
    estimated_usd: float | None
    basis: str
