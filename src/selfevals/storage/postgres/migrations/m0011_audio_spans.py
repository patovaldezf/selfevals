"""m0011 — stt/tts detail tables for voice-turn spans (trace schema 1.5.0).

`SttSpan`/`TtsSpan` are first-class, provider-agnostic legs of a voice turn:
audio→transcript and text→audio. The audio bytes live in the object store
(referenced by `audio_pointer` + `audio_mime_type`); transcript/text inline when
small. One table each, mirroring the existing per-kind detail tables. Additive.
"""

from __future__ import annotations

from typing import Any

_SQL = """
-- Extend the parent trace_spans.kind check to admit the two new voice kinds.
ALTER TABLE trace_spans DROP CONSTRAINT IF EXISTS trace_spans_kind_check;
ALTER TABLE trace_spans ADD CONSTRAINT trace_spans_kind_check CHECK (kind IN (
    'agent_turn', 'llm_call', 'tool_call', 'retrieval', 'memory_read',
    'memory_write', 'decision', 'handoff', 'human_intervention',
    'guardrail_check', 'stt', 'tts', 'error', 'custom'
));

CREATE TABLE IF NOT EXISTS trace_stt_spans (
    trace_id TEXT NOT NULL,
    span_id  TEXT NOT NULL,
    PRIMARY KEY (trace_id, span_id),
    FOREIGN KEY (trace_id, span_id) REFERENCES trace_spans (trace_id, span_id) ON DELETE CASCADE,
    provider           TEXT NOT NULL,
    model              TEXT,
    audio_pointer      TEXT,
    audio_hash         TEXT,
    audio_duration_ms  INTEGER,
    audio_mime_type    TEXT,
    transcript_pointer TEXT,
    transcript_hash    TEXT,
    transcript_inline  TEXT,
    language           TEXT,
    confidence         DOUBLE PRECISION,
    streaming          BOOLEAN NOT NULL DEFAULT FALSE,
    time_to_first_transcript_ms INTEGER,
    provider_metadata  JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS trace_tts_spans (
    trace_id TEXT NOT NULL,
    span_id  TEXT NOT NULL,
    PRIMARY KEY (trace_id, span_id),
    FOREIGN KEY (trace_id, span_id) REFERENCES trace_spans (trace_id, span_id) ON DELETE CASCADE,
    provider          TEXT NOT NULL,
    model             TEXT,
    voice_id          TEXT,
    text_pointer      TEXT,
    text_hash         TEXT,
    text_inline       TEXT,
    audio_pointer     TEXT,
    audio_hash        TEXT,
    audio_duration_ms INTEGER,
    audio_mime_type   TEXT,
    time_to_first_byte_ms INTEGER,
    provider_metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);
"""


def up(cur: Any) -> None:
    cur.execute(_SQL)
