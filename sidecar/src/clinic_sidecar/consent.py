"""Consent gate for a consultation.

§ 201 StGB: recording non-public speech needs the agreement of everyone whose
words are recorded, the doctor included. So a session may only record when a
doctor and a patient are present and every person present has agreed. Anyone
declining, or being unable to consent, blocks recording and the doctor
documents manually instead. Decisions are appended to `consent_events` and
never edited, so the log shows exactly who agreed to what and when.
"""

import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from clinic_sidecar.db import Database

Role = Literal["doctor", "patient", "companion", "interpreter", "student", "other"]
Decision = Literal["granted", "declined", "unable"]
Mode = Literal["consent", "manual"]

ROLE_TITLES: dict[str, str] = {
    "doctor": "Doctor",
    "patient": "Patient",
    "companion": "Companion",
    "interpreter": "Interpreter",
    "student": "Student",
    "other": "Other person",
}


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


@dataclass
class ParticipantView:
    id: str
    role: Role
    label: str | None
    display_name: str
    added_at: str
    decision: Decision | Literal["pending"]
    decided_at: str | None


BlockerCode = Literal["manual", "add_doctor", "add_patient", "pending", "declined", "unable"]


@dataclass
class Blocker:
    """Why recording is locked, as a code the app can show in its own language."""

    code: BlockerCode
    participant_id: str | None = None


@dataclass
class SessionView:
    id: str
    created_at: str
    mode: Mode
    participants: list[ParticipantView]
    recording_allowed: bool
    blockers: list[str] = field(default_factory=list)
    blocker_items: list[Blocker] = field(default_factory=list)


@dataclass
class ConsentEvent:
    participant_id: str
    display_name: str
    role: Role
    decision: Decision
    recorded_at: str


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _require_session(conn: sqlite3.Connection, session_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        raise NotFound(f"Session {session_id} not found")
    return row


def _display_names(rows: list[sqlite3.Row]) -> dict[str, str]:
    """Label if given, else the role title, numbered when a role appears twice."""
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["role"]] = counts.get(row["role"], 0) + 1
    seen: dict[str, int] = {}
    names: dict[str, str] = {}
    for row in rows:
        if row["label"]:
            names[row["id"]] = row["label"]
            continue
        seen[row["role"]] = seen.get(row["role"], 0) + 1
        title = ROLE_TITLES[row["role"]]
        names[row["id"]] = f"{title} {seen[row['role']]}" if counts[row["role"]] > 1 else title
    return names


_BLOCKER_TEXT: dict[str, str] = {
    "manual": "Switched to manual documentation for this consultation.",
    "add_doctor": "Add the doctor.",
    "add_patient": "Add the patient.",
    "pending": "{name} has not answered yet.",
    "declined": "{name} declined. Document manually.",
    "unable": "{name} cannot consent. Document manually.",
}


def _blocker_items(mode: Mode, participants: list[ParticipantView]) -> list[Blocker]:
    if mode == "manual":
        return [Blocker("manual")]
    blockers: list[Blocker] = []
    roles = {p.role for p in participants}
    if "doctor" not in roles:
        blockers.append(Blocker("add_doctor"))
    if "patient" not in roles:
        blockers.append(Blocker("add_patient"))
    for p in participants:
        if p.decision != "granted":
            blockers.append(Blocker(p.decision, p.id))
    return blockers


def _blocker_text(blocker: Blocker, participants: list[ParticipantView]) -> str:
    names = {p.id: p.display_name for p in participants}
    return _BLOCKER_TEXT[blocker.code].format(name=names.get(blocker.participant_id or "", ""))


class ConsentService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create_session(self) -> SessionView:
        session_id = uuid.uuid4().hex
        with self.db.connect() as conn:
            conn.execute("INSERT INTO sessions (id, created_at) VALUES (?, ?)", (session_id, _now()))
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> SessionView:
        with self.db.connect() as conn:
            session = _require_session(conn, session_id)
            rows = conn.execute(
                "SELECT * FROM participants WHERE session_id = ? ORDER BY added_at, rowid",
                (session_id,),
            ).fetchall()
            latest = {
                r["participant_id"]: r
                for r in conn.execute(
                    "SELECT * FROM consent_events WHERE session_id = ? ORDER BY id", (session_id,)
                )
            }
        names = _display_names(rows)
        participants = [
            ParticipantView(
                id=r["id"],
                role=r["role"],
                label=r["label"],
                display_name=names[r["id"]],
                added_at=r["added_at"],
                decision=latest[r["id"]]["decision"] if r["id"] in latest else "pending",
                decided_at=latest[r["id"]]["recorded_at"] if r["id"] in latest else None,
            )
            for r in rows
        ]
        items = _blocker_items(session["mode"], participants)
        blockers = [_blocker_text(b, participants) for b in items]
        return SessionView(
            id=session["id"],
            created_at=session["created_at"],
            mode=session["mode"],
            participants=participants,
            recording_allowed=not blockers,
            blockers=blockers,
            blocker_items=items,
        )

    def recording_allowed(self, session_id: str) -> bool:
        """The one check every recording path must pass."""
        return self.get_session(session_id).recording_allowed

    def add_participant(self, session_id: str, role: Role, label: str | None) -> SessionView:
        label = (label or "").strip() or None
        with self.db.connect() as conn:
            self._require_open(conn, session_id)
            conn.execute(
                "INSERT INTO participants (id, session_id, role, label, added_at) VALUES (?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, session_id, role, label, _now()),
            )
        return self.get_session(session_id)

    def remove_participant(self, session_id: str, participant_id: str) -> SessionView:
        """Only for someone added by mistake. Once a person has answered, their
        record stays; mark a change of mind with a new decision instead."""
        with self.db.connect() as conn:
            self._require_open(conn, session_id)
            self._require_participant(conn, session_id, participant_id)
            answered = conn.execute(
                "SELECT 1 FROM consent_events WHERE participant_id = ?", (participant_id,)
            ).fetchone()
            if answered:
                raise Conflict("This person has already answered and cannot be removed.")
            conn.execute("DELETE FROM participants WHERE id = ?", (participant_id,))
        return self.get_session(session_id)

    def record_decision(self, session_id: str, participant_id: str, decision: Decision) -> SessionView:
        with self.db.connect() as conn:
            self._require_open(conn, session_id)
            self._require_participant(conn, session_id, participant_id)
            conn.execute(
                "INSERT INTO consent_events (session_id, participant_id, decision, recorded_at)"
                " VALUES (?, ?, ?, ?)",
                (session_id, participant_id, decision, _now()),
            )
        return self.get_session(session_id)

    def switch_to_manual(self, session_id: str) -> SessionView:
        with self.db.connect() as conn:
            _require_session(conn, session_id)
            conn.execute("UPDATE sessions SET mode = 'manual' WHERE id = ?", (session_id,))
        return self.get_session(session_id)

    def consent_log(self, session_id: str) -> list[ConsentEvent]:
        view = self.get_session(session_id)
        by_id = {p.id: p for p in view.participants}
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM consent_events WHERE session_id = ? ORDER BY id", (session_id,)
            ).fetchall()
        return [
            ConsentEvent(
                participant_id=r["participant_id"],
                display_name=by_id[r["participant_id"]].display_name,
                role=by_id[r["participant_id"]].role,
                decision=r["decision"],
                recorded_at=r["recorded_at"],
            )
            for r in rows
        ]

    @staticmethod
    def _require_open(conn: sqlite3.Connection, session_id: str) -> None:
        if _require_session(conn, session_id)["mode"] == "manual":
            raise Conflict("This consultation is being documented manually.")

    @staticmethod
    def _require_participant(conn: sqlite3.Connection, session_id: str, participant_id: str) -> None:
        row = conn.execute(
            "SELECT 1 FROM participants WHERE id = ? AND session_id = ?", (participant_id, session_id)
        ).fetchone()
        if row is None:
            raise NotFound(f"Participant {participant_id} not found in this session")
