"""Roundtrip tests for the Arena / ArenaVariant / ArenaRound mappers."""

from __future__ import annotations

from selfevals.schemas.arena import Arena, ArenaBudget, ArenaRound, ArenaVariant, RoundEntry
from selfevals.schemas.enums import ArenaRoundState, ArenaState, ArenaVariantState
from selfevals.schemas.workspace import Workspace
from selfevals.storage.factory import open_storage
from selfevals.storage.interface import ListFilter

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _arena(**overrides: object) -> Arena:
    defaults: dict[str, object] = dict(
        id=Arena.make_id(),
        workspace_id=WS,
        name="tts-bakeoff",
        goal="find the cheapest TTS with acceptable latency",
        repo_path="/repo",
        agent_command=["python", "agent.py"],
        spec_template={"experiment": {"name": "x"}},
        objective_metric="pass_rate",
        budget=ArenaBudget(max_rounds=5, max_variants=8),
    )
    defaults.update(overrides)
    return Arena(**defaults)  # type: ignore[arg-type]


def test_arena_roundtrip(db_url: str) -> None:
    storage = open_storage(db_url)
    try:
        with storage.open(WS) as scope:
            scope.put_entity(Workspace(id=WS, workspace_id=WS, slug="t", name="t"))
            arena = _arena()
            scope.put_entity(arena)
            loaded = scope.get_entity(Arena, arena.id)
        assert isinstance(loaded, Arena)
        assert loaded.name == "tts-bakeoff"
        assert loaded.agent_command == ["python", "agent.py"]
        assert loaded.budget.max_rounds == 5
        assert loaded.state == ArenaState.DRAFT
    finally:
        storage.close()


def test_arena_variant_roundtrip(db_url: str) -> None:
    storage = open_storage(db_url)
    try:
        with storage.open(WS) as scope:
            scope.put_entity(Workspace(id=WS, workspace_id=WS, slug="t2", name="t2"))
            arena = _arena(id=Arena.make_id())
            scope.put_entity(arena)
            variant = ArenaVariant(
                id=ArenaVariant.make_id(),
                workspace_id=WS,
                arena_id=arena.id,
                name="ElevenLabs",
                git_ref="arena/elevenlabs",
                resolved_sha="deadbeef",
                worktree_path="/tmp/worktrees/elevenlabs",
                setup_command=["uv", "sync"],
                hypothesis="ElevenLabs has lower latency than AssemblyAI",
                state=ArenaVariantState.READY,
            )
            scope.put_entity(variant)
            loaded = scope.get_entity(ArenaVariant, variant.id)
        assert isinstance(loaded, ArenaVariant)
        assert loaded.name == "ElevenLabs"
        assert loaded.resolved_sha == "deadbeef"
        assert loaded.setup_command == ["uv", "sync"]
        assert loaded.state == ArenaVariantState.READY
    finally:
        storage.close()


def test_arena_round_roundtrip_with_entries(db_url: str) -> None:
    storage = open_storage(db_url)
    try:
        with storage.open(WS) as scope:
            scope.put_entity(Workspace(id=WS, workspace_id=WS, slug="t3", name="t3"))
            arena = _arena(id=Arena.make_id())
            scope.put_entity(arena)
            round_ = ArenaRound(
                id=ArenaRound.make_id(),
                workspace_id=WS,
                arena_id=arena.id,
                index=0,
                entries=[
                    RoundEntry(
                        variant_id="var_a",
                        experiment_id="exp_a",
                        run_job_id="rj_a",
                        status="running",
                    ),
                    RoundEntry(variant_id="var_b"),
                ],
                state=ArenaRoundState.RUNNING,
            )
            scope.put_entity(round_)
            loaded = scope.get_entity(ArenaRound, round_.id)
        assert isinstance(loaded, ArenaRound)
        assert loaded.index == 0
        assert len(loaded.entries) == 2
        assert loaded.entries[0].variant_id == "var_a"
        assert loaded.entries[0].experiment_id == "exp_a"
        assert loaded.entries[1].status == "pending"
        assert loaded.state == ArenaRoundState.RUNNING
    finally:
        storage.close()


def test_arena_round_unique_per_arena_and_index(db_url: str) -> None:
    storage = open_storage(db_url)
    try:
        with storage.open(WS) as scope:
            scope.put_entity(Workspace(id=WS, workspace_id=WS, slug="t4", name="t4"))
            arena = _arena(id=Arena.make_id())
            scope.put_entity(arena)
            scope.put_entity(
                ArenaRound(id=ArenaRound.make_id(), workspace_id=WS, arena_id=arena.id, index=0)
            )
            rounds = scope.list_entities(
                ArenaRound,
                ListFilter(where={"arena_id": arena.id}, order_by="index", order_desc=False),
            )
        assert len(rounds) == 1
        assert rounds[0].index == 0
    finally:
        storage.close()
