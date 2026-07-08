"""Arena: N code variants (git worktrees) competing on the same dataset+graders.

An Arena is the entity that lets a coding agent (Claude Code or similar) run
its own optimization loop *over source code itself* instead of just over
`search_space.model_params`. selfevals never writes code — it only manages
worktrees, launches one child `Experiment` per (variant, round), and exposes
a cross-variant bundle (`arena.bundle`) the agent reads to decide what to try
next. Variants are registered against existing git refs (branches/commits);
the agent creates the branch externally and hands selfevals the ref.

Each (variant, round) becomes its own single-iteration `Experiment` — this
reuses the entire existing pipeline (aggregate, SSE streaming, traces,
failure-modes, decisions) with zero changes to `OptimizationLoop`.
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import Field, field_validator

from selfevals.schemas._base import BaseEntity, NonEmptyStr, SelfEvalsModel
from selfevals.schemas.enums import ArenaRoundState, ArenaState, ArenaVariantState


class ArenaBudget(SelfEvalsModel):
    """Caps on how far an Arena's agent-driven loop may run.

    All caps are optional soft ceilings enforced by `arena.service` before
    launching a round or accepting a new variant — not by the database.
    """

    max_rounds: int | None = Field(default=None, ge=1)
    max_variants: int | None = Field(default=None, ge=1)
    max_cost_usd: float | None = Field(default=None, gt=0.0)


class Arena(BaseEntity):
    """A competition between code variants of one agent, on one dataset.

    `spec_template` carries the shared shape of a child experiment spec
    (dataset ref, graders, run settings, target/decision policy) minus the
    `agent:` block — `arena.service._build_variant_spec` injects a
    variant-specific `agent.cli` block (command + cwd=worktree + env) into a
    copy of this template before launching each child experiment. This is
    the same dict shape `repo.loader.build_spec_from_mapping` already
    accepts as `spec_inline`, so Arena has no YAML surface of its own.
    """

    _id_prefix: ClassVar[str] = "arn"

    name: NonEmptyStr
    goal: NonEmptyStr
    repo_path: NonEmptyStr
    """Absolute path to the git repo containing the agent under test."""
    agent_command: list[NonEmptyStr] = Field(min_length=1)
    """Argv template run per variant; `cwd` is set to that variant's worktree."""
    agent_env: dict[str, str] | None = None
    spec_template: dict[str, Any]
    dataset_id: str | None = None
    objective_metric: NonEmptyStr
    budget: ArenaBudget = Field(default_factory=ArenaBudget)
    current_round: int = Field(default=0, ge=0)
    state: ArenaState = ArenaState.DRAFT
    winner_variant_id: str | None = None

    @field_validator("agent_command")
    @classmethod
    def _no_blank_args(cls, value: list[str]) -> list[str]:
        if any(not arg for arg in value):
            raise ValueError("agent_command entries must be non-empty strings")
        return value


class ArenaVariant(BaseEntity):
    """One named, git-pinned code variant competing inside an Arena.

    `git_ref` is what the agent (or a human) supplied — a branch name, tag,
    or commit-ish. `resolved_sha` is pinned at registration time via
    `arena.worktrees.resolve_ref` so results stay reproducible even if the
    branch moves; a moved branch requires registering a new variant.
    """

    _id_prefix: ClassVar[str] = "var"

    arena_id: NonEmptyStr
    name: NonEmptyStr
    """Friendly display name (e.g. "ElevenLabs", "workflow-simple")."""
    git_ref: NonEmptyStr
    resolved_sha: str | None = None
    worktree_path: str | None = None
    setup_command: list[str] | None = None
    env_overrides: dict[str, str] | None = None
    hypothesis: str | None = None
    """Why the agent created this variant — carried through to the bundle."""
    created_in_round: int = Field(default=0, ge=0)
    state: ArenaVariantState = ArenaVariantState.REGISTERED
    error: str | None = None


class RoundEntry(SelfEvalsModel):
    """One variant's participation in one ArenaRound."""

    variant_id: NonEmptyStr
    experiment_id: str | None = None
    run_job_id: str | None = None
    status: str = "pending"


class ArenaRound(BaseEntity):
    """One parallel launch: every ready variant run against the same round."""

    _id_prefix: ClassVar[str] = "rnd"

    arena_id: NonEmptyStr
    index: int = Field(ge=0)
    entries: list[RoundEntry] = Field(default_factory=list)
    reps: int = Field(default=1, ge=1)
    state: ArenaRoundState = ArenaRoundState.PENDING
    started_at: str | None = None
    finished_at: str | None = None
    summary: dict[str, Any] | None = None
    """Leaderboard snapshot frozen when the round closes (all entries terminal)."""
