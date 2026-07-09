"""Arena: parallel experiments across git-worktree code variants.

selfevals never writes code here — it manages worktrees, launches one child
`Experiment` per (variant, round) via the existing run pipeline, and exposes
a cross-variant bundle an external coding agent reads to decide what to try
next. See `selfevals.schemas.arena` for the entities and `arena.worktrees`
for the git plumbing.
"""

from __future__ import annotations
