"""Roundtrip test for SttSpan/TtsSpan (trace schema 1.5.0, m0011).

A voice turn nests stt → llm_call → tts under a turn span. Guards that both audio
legs survive a write→read cycle with their provider, audio pointer, transcript/
text, and stage timings intact.
"""

from __future__ import annotations

from datetime import UTC, datetime

from selfevals.schemas.enums import SandboxMode, TraceState
from selfevals.schemas.trace import (
    AgentSnapshotRef,
    AgentTurnSpan,
    EnvironmentInfo,
    FinalState,
    RunInfo,
    SttSpan,
    Trace,
    TtsSpan,
)
from selfevals.schemas.workspace import Workspace
from selfevals.storage.factory import open_storage

WS = "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _voice_trace() -> Trace:
    started = datetime(2026, 7, 12, 12, 0, 0, tzinfo=UTC)
    turn = AgentTurnSpan(id="sp_turn", name="voice_turn:0", started_at=started)
    stt = SttSpan(
        id="sp_stt",
        parent_id="sp_turn",
        name="stt",
        started_at=started,
        provider="assemblyai",
        model="best",
        audio_pointer="oss://ws/sha256:aaa",
        audio_duration_ms=3200,
        audio_mime_type="audio/wav",
        transcript_inline="agenda una llamada con Ana",
        language="es",
        confidence=0.94,
        streaming=True,
        time_to_first_transcript_ms=180,
    )
    tts = TtsSpan(
        id="sp_tts",
        parent_id="sp_turn",
        name="tts",
        started_at=started,
        provider="elevenlabs",
        voice_id="rachel",
        text_inline="Listo, agendé la llamada.",
        audio_pointer="oss://ws/sha256:bbb",
        audio_duration_ms=2100,
        audio_mime_type="audio/mpeg",
        time_to_first_byte_ms=95,
    )
    return Trace(
        id=Trace.make_id(),
        workspace_id=WS,
        run=RunInfo(run_id="run_voice"),
        agent=AgentSnapshotRef(agent_id="ag", agent_version=1),
        environment=EnvironmentInfo(
            framework_version="0.16.0",
            runtime="voice",
            sandbox=SandboxMode.MOCK,
            started_at=started,
        ),
        final_state=FinalState(status=TraceState.COMPLETED),
        spans=[turn, stt, tts],
    )


def test_audio_spans_roundtrip(db_url: str) -> None:
    storage = open_storage(db_url)
    try:
        with storage.open(WS) as scope:
            scope.put_entity(Workspace(id=WS, workspace_id=WS, slug="v", name="v"))
            trace = _voice_trace()
            scope.put_entity(trace)
            loaded = scope.get_entity(Trace, trace.id)
        assert isinstance(loaded, Trace)
        stt = next(s for s in loaded.spans if isinstance(s, SttSpan))
        tts = next(s for s in loaded.spans if isinstance(s, TtsSpan))
        assert stt.provider == "assemblyai"
        assert stt.transcript_inline == "agenda una llamada con Ana"
        assert stt.language == "es"
        assert stt.confidence == 0.94
        assert stt.streaming is True
        assert stt.time_to_first_transcript_ms == 180
        assert stt.audio_mime_type == "audio/wav"
        assert tts.provider == "elevenlabs"
        assert tts.voice_id == "rachel"
        assert tts.text_inline == "Listo, agendé la llamada."
        assert tts.audio_pointer == "oss://ws/sha256:bbb"
        assert tts.time_to_first_byte_ms == 95
    finally:
        storage.close()
