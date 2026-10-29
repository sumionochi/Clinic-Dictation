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
