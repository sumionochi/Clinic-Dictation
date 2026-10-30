"""Checked facts → a FHIR R4 document Bundle another clinician's system can read.

Deterministic code, not a model: the facts are already checked against the
transcript, and turning them into FHIR must never invent or drop anything.

    Composition (the document: sections per category, draft until approved)
    Patient, Encounter, Device (this app, as the author)
    Condition            complaint, symptom, condition
    MedicationStatement  medication
    AllergyIntolerance   allergy
    Procedure            surgery
    Observation          social history
    Provenance           one per fact: the transcript lines and exact quote behind it

How present / absent / past is expressed:
    Condition            verificationStatus unconfirmed | refuted, clinicalStatus active | resolved
    MedicationStatement  status active | not-taken | stopped
    AllergyIntolerance   verificationStatus unconfirmed | refuted, clinicalStatus active | resolved;
                         "keine (weiteren) Allergien" becomes SNOMED 716186003 "No known allergy"
    Procedure            status completed | not-done
    Observation          value "ja" | "nein" | "früher"

Facts stay "unconfirmed" even after approval: the doctor confirms that this is
what was said, not that any of it is a diagnosis. Approval makes the Composition
"final" with a legal attestation; before that the document is a "preliminary" draft. Names are kept as free text
(code.text). Coding with ICD-10-GM, ATC or SNOMED is deliberately left out: proposing
codes is where a scribe starts to become a medical device.
"""

import html
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from clinic_sidecar.agents.graph import Contradiction
from clinic_sidecar.agents.schema import Fact, TranscriptLine

_UUID_NAMESPACE = uuid.UUID("6f1b6c35-3f5a-4e8e-9a43-9d6c1f0a2b7e")

SECTION_TITLES = {
    "complaint": "Aufnahmegrund und Beschwerden",
    "symptom": "Aufnahmegrund und Beschwerden",
    "condition": "Vorerkrankungen",
    "surgery": "Voroperationen",
    "medication": "Medikation",
    "allergy": "Allergien und Unverträglichkeiten",
    "social": "Sozialanamnese",
}
ASSERTION_TEXT = {"present": "ja", "absent": "nein (verneint)", "past": "früher"}


@dataclass
class DocumentInput:
    session_id: str
    facts: list[Fact]
    lines: list[TranscriptLine]
    contradictions: list[Contradiction] = field(default_factory=list)
    started_at: str | None = None  # ISO timestamps of the consultation
    ended_at: str | None = None
    extraction_model: str = ""
    app_version: str = "0.1.0"
    approved_at: str | None = None  # set when the doctor approved the record
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))


def _id(session_id: str, name: str) -> str:
    """Stable ids: exporting the same consultation twice gives the same resource ids."""
    return str(uuid.uuid5(_UUID_NAMESPACE, f"{session_id}/{name}"))


def _ref(resource: dict) -> dict:
    return {"reference": f"urn:uuid:{resource['id']}"}


def _narrative(*parts: str) -> dict:
    body = "".join(f"<p>{html.escape(p)}</p>" for p in parts if p)
    return {"status": "generated", "div": f'<div xmlns="http://www.w3.org/1999/xhtml">{body}</div>'}


def _concept(system: str, code: str, display: str | None = None) -> dict:
    coding = {"system": system, "code": code}
    if display:
        coding["display"] = display
    return {"coding": [coding]}


def _fact_label(f: Fact) -> str:
    label = f"{f.name}: {ASSERTION_TEXT[f.assertion]}"
    return f"{label} ({f.details})" if f.details else label


def _condition(f: Fact, rid: str, patient: dict, encounter: dict) -> dict:
    category = "problem-list-item" if f.category == "condition" else "encounter-diagnosis"
    r = {
        "resourceType": "Condition",
        "id": rid,
        "text": _narrative(_fact_label(f)),
        "clinicalStatus": _concept(
            "http://terminology.hl7.org/CodeSystem/condition-clinical",
            {"present": "active", "past": "resolved", "absent": "inactive"}[f.assertion],
        ),
        "verificationStatus": _concept(
            "http://terminology.hl7.org/CodeSystem/condition-ver-status",
            "refuted" if f.assertion == "absent" else "unconfirmed",
        ),
        # Complaint vs. known illness is carried by the standard category and by the
        # Composition section; no home-made code system that receivers cannot resolve.
        "category": [_concept("http://terminology.hl7.org/CodeSystem/condition-category", category)],
        "code": {"text": f.name},
        "subject": _ref(patient),
        "encounter": _ref(encounter),
    }
    if f.details:
        r["note"] = [{"text": f.details}]
    return r


def _medication(f: Fact, rid: str, patient: dict, encounter: dict, asserted: str) -> dict:
    r = {
        "resourceType": "MedicationStatement",
        "id": rid,
        "text": _narrative(_fact_label(f)),
        "status": {"present": "active", "past": "stopped", "absent": "not-taken"}[f.assertion],
        "medicationCodeableConcept": {"text": f.name},
        "subject": _ref(patient),
        "context": _ref(encounter),
        "dateAsserted": asserted,
        "informationSource": _ref(patient),
    }
    if f.details:
        r["dosage"] = [{"text": f.details}]
    return r


def _is_no_known_allergy(f: Fact) -> bool:
    return f.assertion == "absent" and "allergi" in f.name.lower()


def _allergy(f: Fact, rid: str, patient: dict, encounter: dict) -> dict:
    if _is_no_known_allergy(f):
        code = {
            "coding": [{"system": "http://snomed.info/sct", "code": "716186003", "display": "No known allergy"}],
            "text": f.name,
        }
        verification, clinical = "unconfirmed", "active"
    else:
        code = {"text": f.name}
        verification = "refuted" if f.assertion == "absent" else "unconfirmed"
        clinical = {"present": "active", "past": "resolved", "absent": "inactive"}[f.assertion]
    r = {
        "resourceType": "AllergyIntolerance",
        "id": rid,
        "text": _narrative(_fact_label(f)),
        "clinicalStatus": _concept(
            "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical", clinical
        ),
        "verificationStatus": _concept(
            "http://terminology.hl7.org/CodeSystem/allergyintolerance-verification", verification
        ),
        "code": code,
        "patient": _ref(patient),
        "encounter": _ref(encounter),
    }
    if f.details:
        r["note"] = [{"text": f.details}]
    return r


def _procedure(f: Fact, rid: str, patient: dict) -> dict:
    r = {
        "resourceType": "Procedure",
        "id": rid,
        "text": _narrative(_fact_label(f)),
        "status": "not-done" if f.assertion == "absent" else "completed",
        "code": {"text": f.name},
        "subject": _ref(patient),
    }
    if f.details:
        r["performedString"] = f.details
    return r


def _social(f: Fact, rid: str, patient: dict, encounter: dict, effective: str) -> dict:
    value = ASSERTION_TEXT[f.assertion] + (f": {f.details}" if f.details else "")
    return {
        "resourceType": "Observation",
        "id": rid,
        "text": _narrative(_fact_label(f)),
        "status": "preliminary",
        "category": [
            _concept("http://terminology.hl7.org/CodeSystem/observation-category", "social-history", "Social History")
        ],
        "code": {"text": f.name},
        "subject": _ref(patient),
        "encounter": _ref(encounter),
        "effectiveDateTime": effective,
        "performer": [_ref(patient)],  # patient-reported
        "valueString": value,
    }


def _provenance(f: Fact, rid: str, target: dict, device: dict, lines: dict[int, TranscriptLine], recorded: str) -> dict:
    sources = []
    for ev in f.evidence:
        line = lines.get(ev.line)
        speaker = (line.speaker if line else None) or "unbekannt"
        sources.append(
            {
                "role": "source",
                "what": {
                    "identifier": {"system": "urn:clinic-dictation:transcript-line", "value": str(ev.line)},
                    "display": f'Transkript Zeile {ev.line} ({speaker}): "{ev.quote}"',
                },
            }
        )
    return {
        "resourceType": "Provenance",
        "id": rid,
        "text": _narrative(f"Belegt durch: {'; '.join(s['what']['display'] for s in sources)}"),
        "target": [_ref(target)],
        "recorded": recorded,
        "agent": [
            {
                "type": _concept(
                    "http://terminology.hl7.org/CodeSystem/provenance-participant-type", "assembler", "Assembler"
                ),
                "who": _ref(device),
            }
        ],
        "entity": sources,
    }


def build_document(doc: DocumentInput) -> dict:
    sid = doc.session_id
    when = doc.started_at or doc.generated_at
    lines = {ln.line: ln for ln in doc.lines}

    patient = {
        "resourceType": "Patient",
        "id": _id(sid, "patient"),
        "text": _narrative("Patient (in dieser Anwendung nicht identifiziert; Zuordnung im Primärsystem)"),
    }
    encounter = {
        "resourceType": "Encounter",
        "id": _id(sid, "encounter"),
        "text": _narrative("Aufnahmegespräch"),
        "status": "finished",
        "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "IMP", "display": "inpatient encounter"},
        "type": [{"text": "Aufnahmegespräch"}],
        "subject": _ref(patient),
        "period": {"start": when, **({"end": doc.ended_at} if doc.ended_at else {})},
    }
    device = {
        "resourceType": "Device",
        "id": _id(sid, "device"),
        "text": _narrative(f"Clinic Dictation {doc.app_version} (Extraktion: {doc.extraction_model})"),
        "deviceName": [{"name": "Clinic Dictation", "type": "user-friendly-name"}],
        "version": [{"value": doc.app_version}],
        "note": [{"text": f"Extraktionsmodell: {doc.extraction_model}. Lokal ausgeführt, nicht vom Arzt freigegeben."}],
    }

    fact_resources: list[tuple[Fact, dict]] = []
    for i, f in enumerate(doc.facts):
        rid = _id(sid, f"fact/{i}/{f.category}/{f.name}/{f.assertion}")
        if f.category in ("complaint", "symptom", "condition"):
            r = _condition(f, rid, patient, encounter)
        elif f.category == "medication":
            r = _medication(f, rid, patient, encounter, doc.generated_at)
        elif f.category == "allergy":
            r = _allergy(f, rid, patient, encounter)
        elif f.category == "surgery":
            r = _procedure(f, rid, patient)
        else:
            r = _social(f, rid, patient, encounter, when)
        fact_resources.append((f, r))

    provenances = [
        _provenance(f, _id(sid, f"provenance/{r['id']}"), r, device, lines, doc.generated_at)
        for f, r in fact_resources
    ]

    sections: dict[str, dict] = {}
    for f, r in fact_resources:
        title = SECTION_TITLES[f.category]
        section = sections.setdefault(title, {"title": title, "items": [], "entry": []})
        section["items"].append(_fact_label(f))
        section["entry"].append(_ref(r))
    composition_sections = [
        {"title": s["title"], "text": _narrative(*s["items"]), "entry": s["entry"]} for s in sections.values()
    ]
    if doc.contradictions:
        composition_sections.append(
            {
                "title": "Widersprüche im Gespräch (bitte klären)",
                "text": _narrative(
                    *(
                        f"{c.name}: {', '.join(ASSERTION_TEXT[a] for a in c.assertions)} (Zeilen {', '.join(map(str, c.lines))})"
                        for c in doc.contradictions
                    )
                ),
            }
        )
    approved = doc.approved_at is not None
    composition_sections.append(
        {
            "title": "Hinweis",
            "text": _narrative(
                "Automatisch aus dem aufgezeichneten Gespräch erstellt. Jede Angabe verweist auf die "
                "wörtliche Stelle im Transkript (Provenance). Keine Diagnosen.",
                f"Ärztlich geprüft und freigegeben am {doc.approved_at}." if approved else "Nicht ärztlich freigegeben.",
            ),
        }
    )
    composition = {
        "resourceType": "Composition",
        "id": _id(sid, "composition"),
        "text": _narrative("Aufnahmegespräch" + ("" if approved else " (Entwurf, nicht freigegeben)")),
        "status": "final" if approved else "preliminary",
        "type": {
            "coding": [{"system": "http://loinc.org", "code": "34117-2", "display": "History and physical note"}],
            "text": "Aufnahmegespräch",
        },
        "subject": _ref(patient),
        "encounter": _ref(encounter),
        "date": doc.generated_at,
        "author": [_ref(device)],
        "title": "Aufnahmegespräch" if approved else "Aufnahmegespräch (Entwurf)",
        "section": composition_sections,
    }
    if approved:
        # The approving doctor is not identified in this app; the receiving system
        # links the attestation to its own user.
        composition["attester"] = [{"mode": "legal", "time": doc.approved_at}]

    resources = [composition, patient, encounter, device] + [r for _, r in fact_resources] + provenances
    return {
        "resourceType": "Bundle",
        "id": _id(sid, "bundle"),
        "identifier": {"system": "urn:ietf:rfc:3986", "value": f"urn:uuid:{_id(sid, 'bundle-identifier')}"},
        "type": "document",
        "timestamp": doc.generated_at,
        "entry": [{"fullUrl": f"urn:uuid:{r['id']}", "resource": r} for r in resources],
    }
