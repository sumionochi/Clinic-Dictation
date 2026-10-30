"""Recording a consultation and turning it into a transcript.

Audio arrives from the app in ~1 s chunks of 16 kHz mono 16-bit PCM. Each chunk
is appended to `audio/<recording id>.pcm` and flushed to disk before the request
returns, so a crash loses at most the chunk in flight. Every chunk is checked
against the consent gate: if someone joins the room or changes their mind,
the next chunk is refused.

After the recording stops, the transcript is made on the transcription worker,
split by speaker, and saved to the database in one transaction before its status
says it is done. If telling speakers apart fails, the transcript is still saved,
just without speakers.
"""

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from clinic_sidecar.checklist import ChecklistItem, evaluate
from clinic_sidecar.consent import Conflict, ConsentService, NotFound
from clinic_sidecar.db import Database
from clinic_sidecar.diarizer import Diarizer
from clinic_sidecar.live import next_window, transcribe_window
from clinic_sidecar.speaker_merge import split_by_speaker
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
    speaker: str | None
    language: str | None


@dataclass
class SpeakerView:
    speaker: str  # "S1".."S4", in order of first appearance
    participant_id: str | None  # the person present this voice was matched to
    name: str  # their display name, or "Speaker 1" until matched
    seconds: float  # how long this voice spoke
    first_words: str  # helps the doctor tell voices apart


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
    diarization_model: str | None
    diarize_seconds: float | None
    diarization_error: str | None
    audio_deleted_at: str | None
    speakers: list[SpeakerView]
    segments: list[SegmentView]


@dataclass
class LiveLine:
    idx: int
    start: float
    end: float
    text: str


@dataclass
class LiveView:
    recording_id: str
    status: Status
    recorded_s: float
    processed_s: float  # how far the live pass has got
    lines: list[LiveLine]
    checklist: list[ChecklistItem]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class RecordingService:
    def __init__(
        self,
        db: Database,
        data_dir: Path,
        consent: ConsentService,
        transcriber: Transcriber,
        diarizer: Diarizer,
        worker: TranscriptionWorker,
    ) -> None:
        self.db = db
        self.audio_dir = data_dir / "audio"
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self.consent = consent
        self.transcriber = transcriber
        self.diarizer = diarizer
        self.worker = worker
        self._live_busy: set[str] = set()
        self._live_lock = threading.Lock()
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
            samples, live = conn.execute(
                "SELECT samples, live_samples FROM recordings WHERE id = ?", (recording_id,)
            ).fetchone()
        self._maybe_start_live(recording_id, samples - live)
        return samples

    # ── Live pass ────────────────────────────────────────────────────────────

    def _maybe_start_live(self, recording_id: str, pending_samples: int) -> None:
        from clinic_sidecar.live import WINDOW_S

        if pending_samples < WINDOW_S * SAMPLE_RATE:
            return
        with self._live_lock:
            if recording_id in self._live_busy:
                return
            self._live_busy.add(recording_id)
        self.worker.submit(lambda: self._run_live(recording_id))

    def _run_live(self, recording_id: str) -> None:
        """Transcribe every full window of new audio. Advisory only: a failure here
        never touches the recording or the final transcript."""
        try:
            with self.db.connect() as conn:
                row = conn.execute("SELECT * FROM recordings WHERE id = ?", (recording_id,)).fetchone()
            if row is None or row["status"] != "recording":
                return  # stopped meanwhile; the final pass takes over
            audio = pcm16_to_float32(self.audio_path(recording_id).read_bytes())
            start = row["live_samples"]
            while (end := next_window(audio, start)) is not None:
                segments = transcribe_window(self.transcriber, audio, start, end, row["language"])
                with self.db.connect() as conn:
                    first = conn.execute(
                        "SELECT COUNT(*) FROM live_segments WHERE recording_id = ?", (recording_id,)
                    ).fetchone()[0]
                    conn.executemany(
                        "INSERT INTO live_segments (recording_id, idx, start_s, end_s, text) VALUES (?, ?, ?, ?, ?)",
                        [(recording_id, first + i, s.start, s.end, s.text) for i, s in enumerate(segments)],
                    )
                    conn.execute("UPDATE recordings SET live_samples = ? WHERE id = ?", (end, recording_id))
                start = end
        except Exception:  # noqa: BLE001 - the live preview must never break recording
            pass
        finally:
            with self._live_lock:
                self._live_busy.discard(recording_id)

    def live(self, session_id: str) -> LiveView | None:
        rec = self.get(session_id)
        if rec is None:
            return None
        with self.db.connect() as conn:
            row = conn.execute("SELECT live_samples, sample_rate FROM recordings WHERE id = ?", (rec.id,)).fetchone()
            segs = conn.execute(
                "SELECT * FROM live_segments WHERE recording_id = ? ORDER BY idx", (rec.id,)
            ).fetchall()
        lines = [LiveLine(s["idx"], s["start_s"], s["end_s"], s["text"]) for s in segs]
        return LiveView(
            recording_id=rec.id,
            status=rec.status,
            recorded_s=rec.duration_s,
            processed_s=round(row["live_samples"] / row["sample_rate"], 1),
            lines=lines,
            checklist=evaluate([ln.text for ln in lines]),
        )

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
        if row["audio_deleted_at"]:
            raise Conflict("The audio was deleted after the record was approved.")
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
            # Speakers are a refinement: if this fails, keep the transcript without them.
            began = time.monotonic()
            try:
                turns = self.diarizer.diarize(audio)
                labelled, diarization_error = split_by_speaker(segments, turns), None
            except Exception as exc:  # noqa: BLE001
                labelled, diarization_error = [(s, None) for s in segments], f"{type(exc).__name__}: {exc}"
            diarize_s = round(time.monotonic() - began, 2)
            self._save_transcript(recording_id, labelled, elapsed, diarize_s, diarization_error)
        except Exception as exc:  # noqa: BLE001 - any failure must reach the user
            with self.db.connect() as conn:
                conn.execute(
                    "UPDATE recordings SET status = 'failed', error = ? WHERE id = ?",
                    (f"{type(exc).__name__}: {exc}", recording_id),
                )

    def _save_transcript(
        self,
        recording_id: str,
        segments: list[tuple[Segment, str | None]],
        elapsed: float,
        diarize_s: float,
        diarization_error: str | None,
    ) -> None:
        # Segments and the 'transcribed' status land in one transaction: the
        # transcript is complete on disk before anyone is told it is ready.
        with self.db.connect() as conn:
            conn.execute("DELETE FROM transcript_segments WHERE recording_id = ?", (recording_id,))
            conn.executemany(
                "INSERT INTO transcript_segments"
                " (recording_id, idx, start_s, end_s, text, words, avg_logprob, no_speech_prob, speaker, language)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                        speaker,
                        s.language,
                    )
                    for i, (s, speaker) in enumerate(segments)
                ],
            )
            # A new transcript may find different voices; earlier matches no longer apply.
            conn.execute("DELETE FROM recording_speakers WHERE recording_id = ?", (recording_id,))
            conn.executemany(
                "INSERT INTO recording_speakers (recording_id, speaker) VALUES (?, ?)",
                [(recording_id, spk) for spk in sorted({spk for _, spk in segments if spk})],
            )
            conn.execute(
                "UPDATE recordings SET status = 'transcribed', model = ?, transcribe_seconds = ?,"
                " diarization_model = ?, diarize_seconds = ?, diarization_error = ? WHERE id = ?",
                (
                    self.transcriber.model_name,
                    elapsed,
                    None if diarization_error else self.diarizer.model_name,
                    diarize_s,
                    diarization_error,
                    recording_id,
                ),
            )

    def delete_audio(self, session_id: str) -> None:
        """Called once the doctor has approved the record: the recording itself is no
        longer needed, and keeping it would only be a privacy risk."""
        rec = self.get(session_id)
        if rec is None:
            return
        self.audio_path(rec.id).unlink(missing_ok=True)
        with self.db.connect() as conn:
            conn.execute("UPDATE recordings SET audio_deleted_at = ? WHERE id = ?", (_now(), rec.id))

    # ── Speakers ─────────────────────────────────────────────────────────────

    def match_speaker(
        self, session_id: str, recording_id: str, speaker: str, participant_id: str | None
    ) -> RecordingView:
        """Say which person present a detected voice belongs to. Two voices may map to
        the same person: the diarizer sometimes splits one speaker in two."""
        self._require(session_id, recording_id)
        with self.db.connect() as conn:
            known = conn.execute(
                "SELECT 1 FROM recording_speakers WHERE recording_id = ? AND speaker = ?",
                (recording_id, speaker),
            ).fetchone()
            if known is None:
                raise NotFound(f"No voice {speaker} in this recording")
            if participant_id is not None and conn.execute(
                "SELECT 1 FROM participants WHERE id = ? AND session_id = ?", (participant_id, session_id)
            ).fetchone() is None:
                raise NotFound(f"Participant {participant_id} not found in this session")
            conn.execute(
                "UPDATE recording_speakers SET participant_id = ? WHERE recording_id = ? AND speaker = ?",
                (participant_id, recording_id, speaker),
            )
        return self.get(session_id)  # type: ignore[return-value]

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
            matches = {
                r["speaker"]: r["participant_id"]
                for r in conn.execute(
                    "SELECT speaker, participant_id FROM recording_speakers WHERE recording_id = ?", (row["id"],)
                )
            }
        names = {p.id: p.display_name for p in self.consent.get_session(session_id).participants}
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
            diarization_model=row["diarization_model"],
            diarize_seconds=row["diarize_seconds"],
            diarization_error=row["diarization_error"],
            audio_deleted_at=row["audio_deleted_at"],
            speakers=[
                SpeakerView(
                    speaker=spk,
                    participant_id=pid,
                    name=names.get(pid, f"Speaker {spk[1:]}") if pid else f"Speaker {spk[1:]}",
                    seconds=round(sum(s["end_s"] - s["start_s"] for s in segs if s["speaker"] == spk), 1),
                    first_words=next((s["text"] for s in segs if s["speaker"] == spk), ""),
                )
                for spk, pid in sorted(matches.items())
            ],
            segments=[
                SegmentView(
                    idx=s["idx"],
                    start=s["start_s"],
                    end=s["end_s"],
                    text=s["text"],
                    words=json.loads(s["words"]),
                    speaker=s["speaker"],
                    language=s["language"],
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
