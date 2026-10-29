"""Recording a consultation and turning it into a transcript.

Audio arrives from the app in ~1 s chunks of 16 kHz mono 16-bit PCM. Each chunk
is appended to `audio/<recording id>.pcm` and flushed to disk before the request
returns, so a crash loses at most the chunk in flight. Every chunk is checked
against the consent gate: if someone joins the room or changes their mind,
the next chunk is refused.

After the recording stops, the transcript is made on the transcription worker
and saved to the database in one transaction before its status says it is done.
"""

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from clinic_sidecar.consent import Conflict, ConsentService, NotFound
from clinic_sidecar.db import Database
from clinic_sidecar.transcriber import (
    SAMPLE_RATE,
    Segment,
    Transcriber,
    TranscriptionWorker,
    pcm16_to_float32,
)

Language = Literal["de", "en"]
Status = Literal["recording", "recorded", "transcribing", "transcribed", "failed", "interrupted"]

# Statuses from which a transcript can be (re)made.
TRANSCRIBABLE = ("recorded", "failed", "interrupted")


@dataclass
class SegmentView:
    idx: int
    start: float
    end: float
    text: str
    words: list[dict]


@dataclass
class RecordingView:
    id: str
    session_id: str
    status: Status
    language: Language | None
    duration_s: float
    started_at: str
    stopped_at: str | None
    model: str | None
    transcribe_seconds: float | None
    error: str | None
    segments: list[SegmentView]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class RecordingService:
    def __init__(
        self,
        db: Database,
        data_dir: Path,
        consent: ConsentService,
        transcriber: Transcriber,
        worker: TranscriptionWorker,
    ) -> None:
        self.db = db
        self.audio_dir = data_dir / "audio"
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self.consent = consent
        self.transcriber = transcriber
        self.worker = worker
        self._recover_interrupted()

    def audio_path(self, recording_id: str) -> Path:
        return self.audio_dir / f"{recording_id}.pcm"

    # ── Recording ────────────────────────────────────────────────────────────

    def start(self, session_id: str, language: Language | None) -> RecordingView:
        session = self.consent.get_session(session_id)
        if not session.recording_allowed:
            raise Conflict("Recording is locked: " + " ".join(session.blockers))
        recording_id = uuid.uuid4().hex
        with self.db.connect() as conn:
            if conn.execute("SELECT 1 FROM recordings WHERE session_id = ?", (session_id,)).fetchone():
                raise Conflict("This consultation already has a recording.")
            conn.execute(
                "INSERT INTO recordings (id, session_id, status, language, sample_rate, started_at)"
                " VALUES (?, ?, 'recording', ?, ?, ?)",
                (recording_id, session_id, language, SAMPLE_RATE, _now()),
            )
        self.audio_path(recording_id).touch()
        return self.get(session_id)  # type: ignore[return-value]

    def append_audio(self, session_id: str, recording_id: str, chunk: bytes) -> int:
        """Append one chunk; returns the total number of samples saved so far."""
        if len(chunk) % 2:
            raise Conflict("Audio must be 16-bit PCM (an even number of bytes).")
        row = self._require(session_id, recording_id)
        if row["status"] != "recording":
            raise Conflict(f"Recording is {row['status']}, not accepting audio.")
        if not self.consent.recording_allowed(session_id):
            raise Conflict("Consent changed: recording is no longer allowed.")
        with open(self.audio_path(recording_id), "ab") as f:
            f.write(chunk)
            f.flush()
            os.fsync(f.fileno())
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE recordings SET samples = samples + ? WHERE id = ?", (len(chunk) // 2, recording_id)
            )
            return conn.execute("SELECT samples FROM recordings WHERE id = ?", (recording_id,)).fetchone()[0]

    def stop(self, session_id: str, recording_id: str) -> RecordingView:
        row = self._require(session_id, recording_id)
        if row["status"] != "recording":
            raise Conflict(f"Recording is already {row['status']}.")
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE recordings SET status = 'recorded', stopped_at = ? WHERE id = ?",
                (_now(), recording_id),
            )
        return self.transcribe(session_id, recording_id)

    # ── Transcription ────────────────────────────────────────────────────────

    def transcribe(self, session_id: str, recording_id: str) -> RecordingView:
        row = self._require(session_id, recording_id)
        if row["status"] not in TRANSCRIBABLE:
            raise Conflict(f"Recording is {row['status']}; it cannot be transcribed now.")
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE recordings SET status = 'transcribing', error = NULL WHERE id = ?", (recording_id,)
            )
        self.worker.submit(lambda: self._run_transcription(recording_id, row["language"]))
        return self.get(session_id)  # type: ignore[return-value]

    def _run_transcription(self, recording_id: str, language: str | None) -> None:
        try:
            audio = pcm16_to_float32(self.audio_path(recording_id).read_bytes())
            if audio.size == 0:
                raise ValueError("No audio was recorded.")
            began = time.monotonic()
            segments = self.transcriber.transcribe(audio, language)
            elapsed = round(time.monotonic() - began, 2)
            self._save_transcript(recording_id, segments, elapsed)
        except Exception as exc:  # noqa: BLE001 - any failure must reach the user
            with self.db.connect() as conn:
                conn.execute(
                    "UPDATE recordings SET status = 'failed', error = ? WHERE id = ?",
                    (f"{type(exc).__name__}: {exc}", recording_id),
                )

    def _save_transcript(self, recording_id: str, segments: list[Segment], elapsed: float) -> None:
        # Segments and the 'transcribed' status land in one transaction: the
        # transcript is complete on disk before anyone is told it is ready.
        with self.db.connect() as conn:
            conn.execute("DELETE FROM transcript_segments WHERE recording_id = ?", (recording_id,))
            conn.executemany(
                "INSERT INTO transcript_segments"
                " (recording_id, idx, start_s, end_s, text, words, avg_logprob, no_speech_prob)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        recording_id,
                        i,
                        s.start,
                        s.end,
                        s.text,
                        json.dumps([asdict(w) for w in s.words], ensure_ascii=False),
                        s.avg_logprob,
                        s.no_speech_prob,
                    )
                    for i, s in enumerate(segments)
                ],
            )
            conn.execute(
                "UPDATE recordings SET status = 'transcribed', model = ?, transcribe_seconds = ?"
                " WHERE id = ?",
                (self.transcriber.model_name, elapsed, recording_id),
            )

    # ── Reading ──────────────────────────────────────────────────────────────

    def get(self, session_id: str) -> RecordingView | None:
        self.consent.get_session(session_id)  # 404 for unknown sessions
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM recordings WHERE session_id = ?", (session_id,)).fetchone()
            if row is None:
                return None
            segs = conn.execute(
                "SELECT * FROM transcript_segments WHERE recording_id = ? ORDER BY idx", (row["id"],)
            ).fetchall()
        return RecordingView(
            id=row["id"],
            session_id=row["session_id"],
            status=row["status"],
            language=row["language"],
            duration_s=round(row["samples"] / row["sample_rate"], 2),
            started_at=row["started_at"],
            stopped_at=row["stopped_at"],
            model=row["model"],
            transcribe_seconds=row["transcribe_seconds"],
            error=row["error"],
            segments=[
                SegmentView(
                    idx=s["idx"], start=s["start_s"], end=s["end_s"], text=s["text"], words=json.loads(s["words"])
                )
                for s in segs
            ],
        )

    def _require(self, session_id: str, recording_id: str):
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM recordings WHERE id = ? AND session_id = ?", (recording_id, session_id)
            ).fetchone()
        if row is None:
            raise NotFound(f"Recording {recording_id} not found in this session")
        return row

    def _recover_interrupted(self) -> None:
        """After a crash or force quit, recordings left mid-way keep their saved audio
        and become 'interrupted' so the doctor can still transcribe them."""
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT id FROM recordings WHERE status IN ('recording', 'transcribing')"
            ).fetchall()
            for row in rows:
                path = self.audio_path(row["id"])
                samples = path.stat().st_size // 2 if path.exists() else 0
                conn.execute(
                    "UPDATE recordings SET status = 'interrupted', samples = ? WHERE id = ?",
                    (samples, row["id"]),
                )
