"""Workspace seeding utilities.

`seed_workspace` is the canonical "fresh install" helper: it creates a
workspace, adds the calling user as an admin Member, and registers the
six canonical Role-based memberships requested by the MVP brief
(viewer/evaluator/experimenter/maintainer/admin/auditor — all bound to
the same user since MVP is single-tenant in practice but multi-tenant
in shape).

The result is idempotent on `slug`: if a workspace with the same slug
already exists for any owner, `seed_workspace` is a no-op and returns
the existing workspace.
"""

from __future__ import annotations

from dataclasses import dataclass

from selfevals.schemas.enums import FailureModeStatus, Role
from selfevals.schemas.failure_mode import FailureMode
from selfevals.schemas.workspace import Member, Workspace
from selfevals.storage.interface import ListFilter, StorageInterface

# Industry-common failure modes (LangSmith Insights / Hamel & Shankar). Seeded
# as OFFICIAL so a fresh workspace isn't starting the taxonomy from zero. See
# docs/spec/error_analysis_design.md §2.
CANONICAL_FAILURE_MODES: list[tuple[str, str, str]] = [
    (
        "groundedness_miss",
        "Groundedness miss",
        "Asserts something unsupported by the provided context or sources.",
    ),
    (
        "refusal_over_trigger",
        "Refusal over-trigger",
        "Refuses or hedges on a request that is in fact answerable and allowed.",
    ),
    (
        "refusal_under_trigger",
        "Refusal under-trigger",
        "Answers a request that should have been refused or escalated.",
    ),
    (
        "tool_call_arg_mismatch",
        "Tool-call argument mismatch",
        "Calls the right tool with wrong, malformed, or missing arguments.",
    ),
    (
        "tool_call_wrong_tool",
        "Tool-call wrong tool",
        "Invokes a tool other than the one the task required.",
    ),
    ("agent_loop", "Agent loop", "Repeats the same step or thought without making progress."),
    (
        "retrieval_miss",
        "Retrieval miss",
        "Fails to retrieve the relevant document that was available.",
    ),
    (
        "schema_violation",
        "Schema violation",
        "Output does not conform to the required structure or schema.",
    ),
    ("hallucinated", "Hallucinated", "Fabricates a fact, entity, or value with no basis in input."),
]


@dataclass(frozen=True)
class SeededWorkspace:
    workspace: Workspace
    members: list[Member]


def seed_workspace(
    storage: StorageInterface,
    *,
    slug: str,
    name: str,
    user_id: str,
    description: str | None = None,
    assign_all_roles: bool = True,
) -> SeededWorkspace:
    """Create-or-fetch a workspace + member rows for `user_id`.

    If a workspace with `slug` already exists (across any owner), this is
    a no-op: return the existing workspace and all its members (filtered
    to `user_id` if `assign_all_roles=True` is what we'd otherwise create).

    Roles created: admin always; the rest only if `assign_all_roles=True`.
    """
    # Look across workspaces for an existing slug. There is no global query
    # in MVP — we look inside `user_id`'s scope by convention. A future
    # admin-mode lookup will live elsewhere.
    existing = _find_workspace_by_slug(storage, slug=slug, user_id=user_id)
    if existing is not None:
        existing_members = _list_members(storage, workspace_id=existing.id)
        return SeededWorkspace(workspace=existing, members=existing_members)

    ws_id = Workspace.make_id()
    workspace = Workspace(
        id=ws_id,
        workspace_id=ws_id,
        slug=slug,
        name=name,
        description=description,
        owner_id=user_id,
    )
    roles_to_assign: list[Role] = list(Role) if assign_all_roles else [Role.ADMIN]
    members: list[Member] = [
        Member(
            id=Member.make_id(),
            workspace_id=ws_id,
            user_id=user_id,
            role=role,
        )
        for role in roles_to_assign
    ]

    with storage.open(ws_id) as scope:
        scope.put_entity(workspace)
        for member in members:
            scope.put_entity(member)

    return SeededWorkspace(workspace=workspace, members=members)


def seed_failure_taxonomy(storage: StorageInterface, *, workspace_id: str) -> list[FailureMode]:
    """Seed the canonical failure-mode vocabulary into a workspace.

    Idempotent on slug: modes whose slug already exists are left untouched, so
    re-seeding never duplicates or clobbers human edits. Returns the modes that
    now exist for the canonical slugs (created or pre-existing).
    """
    with storage.open(workspace_id) as scope:
        existing = {
            fm.slug: fm
            for fm in scope.list_entities(FailureMode, ListFilter())
            if isinstance(fm, FailureMode)
        }
        out: list[FailureMode] = []
        for slug, title, definition in CANONICAL_FAILURE_MODES:
            if slug in existing:
                out.append(existing[slug])
                continue
            mode = FailureMode(
                id=FailureMode.make_id(),
                workspace_id=workspace_id,
                slug=slug,
                title=title,
                definition=definition,
                status=FailureModeStatus.OFFICIAL,
                proposed_by="seed",
            )
            scope.put_entity(mode)
            out.append(mode)
    return out


def _find_workspace_by_slug(
    storage: StorageInterface, *, slug: str, user_id: str
) -> Workspace | None:
    """Look for a workspace owned by `user_id` with `slug`.

    Since workspaces have self-referential workspace_id, we look one at a
    time: workspace storage rows are themselves listable by `slug`. We
    iterate candidate workspace_ids by opening scopes per known owner. In
    MVP we only know about the seeding user — that's enough for idempotency.

    The trick: list any Workspace entities whose own row exists under
    workspace_id == workspace.id. We scan via the underlying entities
    table directly through a private one-shot helper. To keep the API
    clean we cheat slightly and use the SQLite path; abstraction will
    move into the interface if Postgres ever needs to do this.
    """
    # We can't iterate every workspace via the typed interface (there is
    # no "all workspaces" view in WorkspaceScope on purpose). For
    # seed_workspace we only need idempotency by slug+owner, so we use
    # the raw connection when available.
    sqlite_attr = getattr(storage, "connection", None)
    if sqlite_attr is None:
        return None
    conn = sqlite_attr
    row = conn.execute(
        "SELECT workspace_id FROM entities "
        "WHERE entity_type = 'Workspace' "
        "AND json_extract(payload, '$.slug') = ? "
        "AND json_extract(payload, '$.owner_id') = ?",
        (slug, user_id),
    ).fetchone()
    if row is None:
        return None
    workspace_id: str = row[0]
    with storage.open(workspace_id) as scope:
        return scope.get_entity(Workspace, workspace_id)  # type: ignore[return-value]


def _list_members(storage: StorageInterface, *, workspace_id: str) -> list[Member]:
    with storage.open(workspace_id) as scope:
        return scope.list_entities(Member, ListFilter())  # type: ignore[return-value]
