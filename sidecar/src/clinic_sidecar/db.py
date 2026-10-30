"""SQLite storage for the sidecar.

One file, `clinic.sqlite`, in the data directory Electron passes in. Each call
opens a short-lived connection, so FastAPI's worker threads never share one.
Writes are committed before the request returns: data is saved before anything
is shown to the user.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

# Each entry upgrades the schema by one version. Never edit an entry that has
# shipped; append a new one instead.
MIGRATIONS: list[str] = [
    # 1: consultations, the people present, and an append-only consent log
    """
    CREATE TABLE sessions (
        id          TEXT PRIMARY KEY,
        created_at  TEXT NOT NULL,
        mode        TEXT NOT NULL DEFAULT 'consent' CHECK (mode IN ('consent', 'manual'))
    );

    CREATE TABLE participants (
        id          TEXT PRIMARY KEY,
        session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        role        TEXT NOT NULL,
        label       TEXT,
        added_at    TEXT NOT NULL
    );
    CREATE INDEX participants_session ON participants(session_id);

    CREATE TABLE consent_events (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        participant_id  TEXT NOT NULL REFERENCES participants(id) ON DELETE RESTRICT,
        decision        TEXT NOT NULL CHECK (decision IN ('granted', 'declined', 'unable')),
        recorded_at     TEXT NOT NULL
    );
    CREATE INDEX consent_events_session ON consent_events(session_id);

    -- The log is evidence: rows can be added, never changed or removed.
    CREATE TRIGGER consent_events_no_update BEFORE UPDATE ON consent_events
    BEGIN SELECT RAISE(ABORT, 'consent_events is append-only'); END;
    CREATE TRIGGER consent_events_no_delete BEFORE DELETE ON consent_events
    BEGIN SELECT RAISE(ABORT, 'consent_events is append-only'); END;
    """,
    # 2: one recording per consultation, and the transcript made from it
    """
    CREATE TABLE recordings (
        id                  TEXT PRIMARY KEY,
        session_id          TEXT NOT NULL UNIQUE REFERENCES sessions(id),
        status              TEXT NOT NULL CHECK (status IN (
                                'recording', 'recorded', 'transcribing', 'transcribed',
                                'failed', 'interrupted')),
        language            TEXT,
        sample_rate         INTEGER NOT NULL,
        samples             INTEGER NOT NULL DEFAULT 0,
        started_at          TEXT NOT NULL,
        stopped_at          TEXT,
        model               TEXT,
        transcribe_seconds  REAL,
        error               TEXT
    );

    CREATE TABLE transcript_segments (
        recording_id    TEXT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
        idx             INTEGER NOT NULL,
        start_s         REAL NOT NULL,
        end_s           REAL NOT NULL,
        text            TEXT NOT NULL,
        words           TEXT NOT NULL,   -- JSON list of {word, start, end, probability}
        avg_logprob     REAL,
        no_speech_prob  REAL,
        PRIMARY KEY (recording_id, idx)
    );
    """,
    # 3: who said each line, and which person present each detected voice belongs to
    """
    ALTER TABLE transcript_segments ADD COLUMN speaker TEXT;
    ALTER TABLE recordings ADD COLUMN diarization_model TEXT;
    ALTER TABLE recordings ADD COLUMN diarize_seconds REAL;
    ALTER TABLE recordings ADD COLUMN diarization_error TEXT;

    CREATE TABLE recording_speakers (
        recording_id    TEXT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
        speaker         TEXT NOT NULL,
        participant_id  TEXT REFERENCES participants(id),
        PRIMARY KEY (recording_id, speaker)
    );
    """,
    # 4: the language each line was transcribed in (mixed-language consultations)
    """
    ALTER TABLE transcript_segments ADD COLUMN language TEXT;
    """,
    # 5: the structured record the agents draft and the doctor reviews and approves
    """
    ALTER TABLE recordings ADD COLUMN audio_deleted_at TEXT;

    CREATE TABLE records (
        session_id      TEXT PRIMARY KEY REFERENCES sessions(id),
        recording_id    TEXT NOT NULL REFERENCES recordings(id),
        status          TEXT NOT NULL CHECK (status IN ('extracting', 'ready', 'failed', 'approved')),
        model           TEXT,
        lines           TEXT,   -- JSON: the numbered transcript the facts cite
        facts           TEXT,   -- JSON: [{id, fact, state, origin}], state: kept | removed
        rejected        TEXT,   -- JSON: facts the verifier rejected, with reasons
        contradictions  TEXT,   -- JSON
        log             TEXT,   -- JSON: what the agents did
        error           TEXT,
        extract_seconds REAL,
        created_at      TEXT NOT NULL,
        approved_at     TEXT,
        fhir_bundle     TEXT    -- JSON, written once on approval
    );
    """,
]


class Database:
    def __init__(self, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / "clinic.sqlite"
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            self._migrate(conn)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        for version, script in enumerate(MIGRATIONS[current:], start=current + 1):
            conn.executescript(f"BEGIN; {script}; PRAGMA user_version = {version}; COMMIT;")
