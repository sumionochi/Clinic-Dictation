"""The structured record of a consultation: drafted by the agents, reviewed and
approved by the doctor.

    transcribed recording ──extract──► ready ──(doctor edits)──► approve ──► approved
                                 └──► failed (retry)

- Extraction runs the agent pipeline on the transcript with speaker names, as
  numbered lines. The lines are stored with the record so citations keep pointing
  at the same words.
- The doctor can remove a fact, restore one (including one the verifier rejected),
  or correct its assertion, details or name. The evidence stays attached, and
  every change is logged with its time and the content before and after it:
  § 630f Abs. 1 BGB allows corrections only if the original content and the time
  of the change stay recognisable.
- Approval builds the FHIR document and runs the official validator. Only with
  zero errors is the record approved, the document stored, and then the audio
  deleted. The transcript and the record are kept.
"""

import json
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Literal

from clinic_sidecar.agents.graph import Contradiction, extract_facts, find_contradictions
from clinic_sidecar.agents.llm import LLM
from clinic_sidecar.agents.schema import Assertion, Fact, TranscriptLine
from clinic_sidecar.consent import Conflict, ConsentService, NotFound
from clinic_sidecar.db import Database
from clinic_sidecar.document import pdfa
from clinic_sidecar.document.render import DocumentData, render_html
from clinic_sidecar.fhir.mapper import DocumentInput, build_document
from clinic_sidecar.fhir.validator import ValidationReport
from clinic_sidecar.recording import RecordingService

RecordStatus = Literal["extracting", "ready", "failed", "approved"]
FactState = Literal["kept", "removed"]
FactOrigin = Literal["agents", "verifier_rejected", "doctor_edited"]


@dataclass
class ReviewFact:
    id: str
    fact: Fact
    state: FactState
    origin: FactOrigin
    reason: str | None = None  # why the verifier rejected it, for restored facts
    # Every change the doctor made: {at, action, before, after}, oldest first.
    history: list[dict] = field(default_factory=list)


@dataclass
class RecordView:
    session_id: str
    status: RecordStatus
    model: str | None
    lines: list[TranscriptLine]
    facts: list[ReviewFact]
    contradictions: list[Contradiction]
    log: list[str]
    error: str | None
    extract_seconds: float | None
    created_at: str
    approved_at: str | None
    validation: dict | None  # summary of the validator run at approval


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class RecordService:
    def __init__(
        self,
        db: Database,
        consent: ConsentService,
        recordings: RecordingService,
        llm_factory: Callable[[], LLM],
        validator: Callable[[dict], ValidationReport],
        pdf_to_pdfa: Callable[[bytes, pdfa.PdfMetadata], bytes] = pdfa.to_pdfa,
        pdfa_validator: Callable[[bytes], pdfa.PdfaReport] = pdfa.validate,
    ) -> None:
        self.db = db
        self.consent = consent
        self.recordings = recordings
        self.llm_factory = llm_factory
        self.validator = validator
        self.pdf_to_pdfa = pdf_to_pdfa
        self.pdfa_validator = pdfa_validator
        # LLM calls go over HTTP to Ollama, so they do not need the MLX thread.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agents")
        self._recover()

    # ── Extraction ───────────────────────────────────────────────────────────

    def transcript_lines(self, session_id: str) -> list[TranscriptLine]:
        rec = self.recordings.get(session_id)
        if rec is None or rec.status != "transcribed":
            raise Conflict("The consultation needs a finished transcript first.")
        names = {v.speaker: v.name for v in rec.speakers}
        return [
            TranscriptLine(line=i + 1, speaker=names.get(s.speaker) if s.speaker else None, text=s.text)
            for i, s in enumerate(rec.segments)
        ]

    def start(self, session_id: str) -> RecordView:
        lines = self.transcript_lines(session_id)
        rec = self.recordings.get(session_id)
        with self.db.connect() as conn:
            row = conn.execute("SELECT status FROM records WHERE session_id = ?", (session_id,)).fetchone()
            if row and row["status"] in ("extracting", "approved"):
                raise Conflict(f"The record is already {row['status']}.")
            conn.execute("DELETE FROM records WHERE session_id = ?", (session_id,))
            conn.execute(
                "INSERT INTO records (session_id, recording_id, status, lines, created_at)"
                " VALUES (?, ?, 'extracting', ?, ?)",
                (session_id, rec.id, json.dumps([ln.model_dump() for ln in lines], ensure_ascii=False), _now()),
            )
        self._executor.submit(self._run, session_id, lines)
        return self.get(session_id)  # type: ignore[return-value]

    def _run(self, session_id: str, lines: list[TranscriptLine]) -> None:
        llm = self.llm_factory()
        began = time.monotonic()
        try:
            result = extract_facts(lines, llm)
        except Exception as exc:  # noqa: BLE001 - shown to the doctor, who can retry
            with self.db.connect() as conn:
                conn.execute(
                    "UPDATE records SET status = 'failed', model = ?, error = ? WHERE session_id = ?",
                    (llm.model, f"{type(exc).__name__}: {exc}", session_id),
                )
            return
        facts = [self._review(f, "kept", "agents") for f in result.facts]
        rejected = [self._review(r.fact, "removed", "verifier_rejected", r.reason) for r in result.rejected]
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE records SET status = 'ready', model = ?, facts = ?, rejected = ?, contradictions = ?,"
                " log = ?, error = NULL, extract_seconds = ? WHERE session_id = ?",
                (
                    llm.model,
                    self._dump(facts + rejected),
                    "[]",
                    json.dumps([asdict(c) for c in result.contradictions], ensure_ascii=False),
                    json.dumps(result.log, ensure_ascii=False),
                    round(time.monotonic() - began, 1),
                    session_id,
                ),
            )

    # ── Doctor's review ──────────────────────────────────────────────────────

    def update_fact(
        self,
        session_id: str,
        fact_id: str,
        action: Literal["remove", "restore", "edit"],
        name: str | None = None,
        details: str | None = None,
        assertion: Assertion | None = None,
    ) -> RecordView:
        row = self._require(session_id)
        if row["status"] != "ready":
            raise Conflict(f"The record is {row['status']}; it can only be changed while ready for review.")
        facts = self._load_facts(row)
        target = next((f for f in facts if f.id == fact_id), None)
        if target is None:
            raise NotFound(f"Fact {fact_id} not found")
        before = self._snapshot(target)
        if action == "remove":
            target.state = "removed"
        elif action == "restore":
            target.state = "kept"
        else:
            changes = {
                k: v
                for k, v in {"name": name, "details": details, "assertion": assertion}.items()
                if v is not None
            }
            if "details" in changes and not changes["details"].strip():
                changes["details"] = None
            target.fact = target.fact.model_copy(update=changes)
            target.origin = "doctor_edited"
            target.state = "kept"
        after = self._snapshot(target)
        if after != before:
            target.history.append({"at": _now(), "action": action, "before": before, "after": after})
        kept = [f.fact for f in facts if f.state == "kept"]
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE records SET facts = ?, contradictions = ? WHERE session_id = ?",
                (
                    self._dump(facts),
                    # Recomputed over what the doctor keeps, so resolving a contradiction clears it.
                    json.dumps([asdict(c) for c in find_contradictions(kept)], ensure_ascii=False),
                    session_id,
                ),
            )
        return self.get(session_id)  # type: ignore[return-value]

    # ── Approval ─────────────────────────────────────────────────────────────

    def document(self, session_id: str, approved_at: str | None = None) -> dict:
        row = self._require(session_id)
        rec = self.recordings.get(session_id)
        facts = [f.fact for f in self._load_facts(row) if f.state == "kept"]
        return build_document(
            DocumentInput(
                session_id=session_id,
                facts=facts,
                lines=[TranscriptLine.model_validate(x) for x in json.loads(row["lines"])],
                contradictions=[Contradiction(**c) for c in json.loads(row["contradictions"] or "[]")],
                started_at=rec.started_at if rec else None,
                ended_at=rec.stopped_at if rec else None,
                extraction_model=row["model"] or "",
                approved_at=approved_at,
            )
        )

    def approve(self, session_id: str) -> RecordView:
        row = self._require(session_id)
        if row["status"] != "ready":
            raise Conflict(f"The record is {row['status']}; only a record ready for review can be approved.")
        approved_at = _now()
        bundle = self.document(session_id, approved_at)
        report = self.validator(bundle)
        if not report.valid:
            raise Conflict(
                "The FHIR document did not pass validation, so nothing was approved or exported: "
                + "; ".join(f"{e.location}: {e.message}" for e in report.errors[:5])
            )
        summary = {"errors": 0, "warnings": len(report.warnings), "resources": len(bundle["entry"])}
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE records SET status = 'approved', approved_at = ?, fhir_bundle = ? WHERE session_id = ?",
                (approved_at, json.dumps({"bundle": bundle, "validation": summary}, ensure_ascii=False), session_id),
            )
        # Only after the approved record is safely stored: the audio goes.
        self.recordings.delete_audio(session_id)
        return self.get(session_id)  # type: ignore[return-value]

    def exported_bundle(self, session_id: str) -> dict:
        row = self._require(session_id)
        if row["status"] != "approved":
            raise Conflict("Only an approved record can be exported.")
        return json.loads(row["fhir_bundle"])["bundle"]

    # ── Readable document ────────────────────────────────────────────────────

    def document_data(self, session_id: str) -> DocumentData:
        row = self._require(session_id)
        if row["status"] != "approved":
            raise Conflict("Only an approved record can be exported.")
        stored = json.loads(row["fhir_bundle"])
        rec = self.recordings.get(session_id)
        session = self.consent.get_session(session_id)
        facts = self._load_facts(row)
        return DocumentData(
            document_id=stored["bundle"]["identifier"]["value"],
            started_at=rec.started_at if rec else None,
            stopped_at=rec.stopped_at if rec else None,
            duration_s=rec.duration_s if rec else 0.0,
            approved_at=row["approved_at"],
            audio_deleted_at=rec.audio_deleted_at if rec else None,
            languages=sorted({s.language for s in rec.segments if s.language}) if rec else [],
            participants=[
                {"role": p.role, "display_name": p.display_name, "decision": p.decision, "decided_at": p.decided_at}
                for p in session.participants
            ],
            facts=[{**asdict(f), "fact": f.fact.model_dump()} for f in facts if f.state == "kept"],
            changed=[{**asdict(f), "fact": f.fact.model_dump()} for f in facts if f.history],
            contradictions=json.loads(row["contradictions"] or "[]"),
            lines={ln["line"]: ln for ln in json.loads(row["lines"] or "[]")},
            transcription_model=rec.model if rec else None,
            diarization_model=rec.diarization_model if rec else None,
            extraction_model=row["model"],
        )

    def document_html(self, session_id: str) -> dict:
        data = self.document_data(session_id)
        day = (data.started_at or data.approved_at)[:10]
        return {
            "html": render_html(data),
            "document_id": data.document_id,
            "filename": f"aufnahmegespraech-{day}",
        }

    def document_pdfa(self, session_id: str, printed_pdf: bytes) -> bytes:
        """The printed document as PDF/A-2b, released only if veraPDF accepts it."""
        data = self.document_data(session_id)
        try:
            converted = self.pdf_to_pdfa(
                printed_pdf,
                pdfa.PdfMetadata(
                    title=f"Aufnahmegespräch {data.document_id}",
                    author="Clinic Dictation (KI-gestützt erstellt, ärztlich geprüft und freigegeben)",
                    subject="Strukturierte Anamnese aus einem aufgezeichneten Arzt-Patienten-Gespräch",
                    keywords=f"AI-assisted; human-reviewed; approved {data.approved_at}; FHIR {data.document_id}",
                ),
            )
            report = self.pdfa_validator(converted)
        except pdfa.PdfaUnavailable as exc:
            raise Conflict(f"PDF/A export is not set up: {exc}") from exc
        if not report.compliant:
            raise Conflict("The PDF did not pass PDF/A validation: " + "; ".join(report.failures[:3]))
        return converted

    # ── Reading ──────────────────────────────────────────────────────────────

    def get(self, session_id: str) -> RecordView | None:
        self.consent.get_session(session_id)  # 404 for unknown sessions
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM records WHERE session_id = ?", (session_id,)).fetchone()
        if row is None:
            return None
        return RecordView(
            session_id=session_id,
            status=row["status"],
            model=row["model"],
            lines=[TranscriptLine.model_validate(x) for x in json.loads(row["lines"] or "[]")],
            facts=self._load_facts(row),
            contradictions=[Contradiction(**c) for c in json.loads(row["contradictions"] or "[]")],
            log=json.loads(row["log"] or "[]"),
            error=row["error"],
            extract_seconds=row["extract_seconds"],
            created_at=row["created_at"],
            approved_at=row["approved_at"],
            validation=json.loads(row["fhir_bundle"])["validation"] if row["fhir_bundle"] else None,
        )

    # ── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _review(fact: Fact, state: FactState, origin: FactOrigin, reason: str | None = None) -> ReviewFact:
        return ReviewFact(id=uuid.uuid4().hex[:12], fact=fact, state=state, origin=origin, reason=reason)

    @staticmethod
    def _snapshot(item: ReviewFact) -> dict:
        f = item.fact
        return {"name": f.name, "details": f.details, "assertion": f.assertion, "state": item.state}

    @staticmethod
    def _dump(facts: list[ReviewFact]) -> str:
        return json.dumps(
            [{**asdict(f), "fact": f.fact.model_dump()} for f in facts], ensure_ascii=False
        )

    @staticmethod
    def _load_facts(row) -> list[ReviewFact]:
        return [
            ReviewFact(**{**f, "fact": Fact.model_validate(f["fact"])}) for f in json.loads(row["facts"] or "[]")
        ]

    def _require(self, session_id: str):
        self.consent.get_session(session_id)
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM records WHERE session_id = ?", (session_id,)).fetchone()
        if row is None:
            raise NotFound("No record for this consultation yet")
        return row

    def _recover(self) -> None:
        """An extraction cut off by a restart is marked failed, so it can be retried."""
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE records SET status = 'failed', error = 'Interrupted (the app was closed); try again.'"
                " WHERE status = 'extracting'"
            )
