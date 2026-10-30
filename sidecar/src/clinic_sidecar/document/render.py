"""The approved record as a readable German document, for people.

The FHIR export is for systems; this is what a doctor reads, files or forwards.
It is built to the German and EU rules that apply to such a document:

- § 630f Abs. 2 BGB: the treatment record covers the medical history (Anamnese)
  and the consents given. So it lists who was present and when each person agreed
  to the recording (§ 201 StGB), next to the structured history.
- § 630f Abs. 1 BGB: corrections must leave the original content and the time of
  the change recognisable. Every change the doctor made during review is listed in
  an amendment log, with the content before and after.
- KBV eArztbrief guideline (§ 383 SGB V): the letter's full content travels as
  PDF/A. The document is laid out for print and converted to PDF/A-2b
  (document/pdfa.py). The qualified signature with the eHBA, the CDA header with
  the patient's eGK data and KIM transport happen in the certified practice or
  hospital system, which is also where the patient's identity lives.
- GDPR data minimisation: only the facts and the short quotes behind them, not the
  full transcript.
- Transparency about AI (in the spirit of Art. 50 AI Act): it says plainly that the
  content was structured by AI from the recording and reviewed by a doctor.

An empty section says only that nothing was documented for it. It does not claim
the topic never came up (the app cannot know that: a fact may have been missed),
and it is never a denial, which is always written out as "verneint".
"""

import html
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from clinic_sidecar import __version__

BERLIN = ZoneInfo("Europe/Berlin")

SECTIONS: list[tuple[str, tuple[str, ...]]] = [
    ("Aufnahmegrund und aktuelle Beschwerden", ("complaint", "symptom")),
    ("Vorerkrankungen", ("condition",)),
    ("Voroperationen und Eingriffe", ("surgery",)),
    ("Medikation", ("medication",)),
    ("Allergien und Unverträglichkeiten", ("allergy",)),
    ("Sozialanamnese", ("social",)),
]

STATUS = {
    "medication": {"present": "wird eingenommen", "past": "abgesetzt", "absent": "wird nicht eingenommen"},
    "default": {"present": "ja", "past": "früher", "absent": "verneint"},
}

ROLE_DE = {
    "doctor": "Ärztin / Arzt",
    "patient": "Patientin / Patient",
    "companion": "Begleitperson",
    "interpreter": "Dolmetscher/in",
    "student": "Student/in",
    "other": "Weitere Person",
}
# Speaker names on transcript lines are the app's English display names unless the
# doctor gave a description such as "Tochter".
SPEAKER_DE = {"Doctor": "Arzt/Ärztin", "Patient": "Patient/in", "Companion": "Begleitperson",
              "Interpreter": "Dolmetscher/in", "Student": "Student/in", "Other person": "Weitere Person"}
# Numbered defaults ("Companion 2") keep their number.
for _en, _de in list(SPEAKER_DE.items()):
    for _n in range(1, 10):
        SPEAKER_DE[f"{_en} {_n}"] = f"{_de} {_n}"
DECISION_DE = {"granted": "eingewilligt", "declined": "abgelehnt", "unable": "nicht einwilligungsfähig",
               "pending": "keine Angabe"}
ACTION_DE = {"edit": "geändert", "remove": "entfernt", "restore": "wiederhergestellt"}
LANGUAGE_DE = {"de": "Deutsch", "en": "Englisch"}


@dataclass
class DocumentData:
    """Everything the document shows, gathered by RecordService.document_data()."""

    document_id: str  # the FHIR Bundle identifier, so both exports name the same document
    started_at: str | None
    stopped_at: str | None
    duration_s: float
    approved_at: str
    audio_deleted_at: str | None
    languages: list[str]
    participants: list[dict]  # {role, display_name, decision, decided_at}
    facts: list[dict]  # kept ReviewFacts as dicts
    changed: list[dict]  # ReviewFacts with a history, as dicts (kept or removed)
    contradictions: list[dict]
    lines: dict[int, dict]  # line number -> {speaker, text}
    transcription_model: str | None
    diarization_model: str | None
    extraction_model: str | None


def _e(text: object) -> str:
    return html.escape(str(text), quote=True)


def _when(iso: str | None, with_date: bool = True) -> str:
    if not iso:
        return "–"
    t = datetime.fromisoformat(iso).astimezone(BERLIN)
    return t.strftime("%d.%m.%Y, %H:%M Uhr") if with_date else t.strftime("%H:%M")


def _speaker(name: str | None) -> str:
    return SPEAKER_DE.get(name or "", name or "unbekannt")


def _status(category: str, assertion: str) -> str:
    return STATUS.get(category, STATUS["default"])[assertion]


def _evidence(fact: dict, lines: dict[int, dict]) -> str:
    parts = []
    for ev in fact["evidence"]:
        line = lines.get(ev["line"], {})
        parts.append(
            f"„{_e(ev['quote'])}“ <span class='where'>(Z. {ev['line']}, {_e(_speaker(line.get('speaker')))})</span>"
        )
    return "<br>".join(parts)


def _model_name(model: str | None) -> str:
    return (model or "–").split("/")[-1]


def render_html(d: DocumentData) -> str:
    rows_people = "".join(
        f"<tr><td>{_e(_speaker(p['display_name']))}</td><td>{_e(ROLE_DE.get(p['role'], p['role']))}</td>"
        f"<td>{_e(DECISION_DE.get(p['decision'], p['decision']))}</td><td>{_e(_when(p['decided_at']))}</td></tr>"
        for p in d.participants
    )

    sections = []
    for number, (title, categories) in enumerate(SECTIONS, start=1):
        items = [f for f in d.facts if f["fact"]["category"] in categories]
        if not items:
            body = "<p class='none'>Keine Angaben dokumentiert.</p>"
        else:
            first_col = "Präparat / Wirkstoff" if "medication" in categories else "Angabe"
            second_col = "Dosierung / Einnahme" if "medication" in categories else "Details"
            rows = "".join(
                f"<tr><td class='name'>{_e(f['fact']['name'])}"
                f"{' <span class=tag>ärztlich geändert</span>' if f['origin'] == 'doctor_edited' else ''}</td>"
                f"<td>{_e(_status(f['fact']['category'], f['fact']['assertion']))}</td>"
                f"<td>{_e(f['fact']['details'] or '–')}</td>"
                f"<td class='evidence'>{_evidence(f['fact'], d.lines)}</td></tr>"
                for f in items
            )
            body = (
                f"<table class='facts'><colgroup><col class='c1'><col class='c2'><col class='c3'><col class='c4'></colgroup>"
                f"<thead><tr><th>{first_col}</th><th>Status</th><th>{second_col}</th><th>Beleg im Gespräch</th></tr></thead>"
                f"<tbody>{rows}</tbody></table>"
            )
        sections.append(f"<h3>2.{number} {_e(title)}</h3>{body}")

    if d.contradictions:
        contra = "<ul>" + "".join(
            f"<li><b>{_e(c['name'])}</b>: im Gespräch sowohl "
            + " als auch ".join(f"„{_e(_status(c['category'], a))}“" for a in c["assertions"])
            + f" (Zeilen {_e(', '.join(map(str, c['lines'])))}). Bitte mit der Patientin / dem Patienten klären.</li>"
            for c in d.contradictions
        ) + "</ul>"
    else:
        contra = "<p class='none'>Keine Widersprüche erkannt.</p>"

    changes = []
    for f in d.changed:
        for h in f["history"]:
            before, after = h["before"], h["after"]

            def show(s: dict, category: str = f["fact"]["category"]) -> str:
                text = f"{s['name']}: {_status(category, s['assertion'])}"
                if s.get("details"):
                    text += f" ({s['details']})"
                return text if s["state"] == "kept" else f"{text} [nicht übernommen]"

            changes.append(
                f"<tr><td>{_e(_when(h['at']))}</td><td>{_e(ACTION_DE.get(h['action'], h['action']))}</td>"
                f"<td>{_e(show(before))}</td><td>{_e(show(after))}</td></tr>"
            )
    change_log = (
        "<table class='log'><thead><tr><th>Zeitpunkt</th><th>Änderung</th><th>Vorher (Entwurf)</th><th>Nachher</th></tr></thead>"
        f"<tbody>{''.join(changes)}</tbody></table>"
        if changes
        else "<p class='none'>Der Entwurf wurde ohne Änderungen freigegeben.</p>"
    )

    started = _when(d.started_at)
    period = f"{started} – {_when(d.stopped_at, with_date=False)}" if d.stopped_at else started
    minutes = max(1, round(d.duration_s / 60))
    languages = ", ".join(LANGUAGE_DE.get(lang, lang) for lang in d.languages) or "–"

    return f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<title>Aufnahmegespräch – {_e(started)}</title>
<style>
@page {{ size: A4; margin: 16mm 16mm 18mm 16mm; }}
* {{ box-sizing: border-box; }}
body {{ font-family: Arial, Helvetica, sans-serif; font-size: 9.5pt; line-height: 1.35; color: #000; margin: 0; }}
h1 {{ font-size: 17pt; margin: 0 0 2pt; }}
.sub {{ color: #444; margin: 0 0 10pt; }}
h2 {{ font-size: 11.5pt; margin: 14pt 0 5pt; padding-bottom: 2pt; border-bottom: 0.75pt solid #999; }}
h3 {{ font-size: 10pt; margin: 9pt 0 3pt; }}
table {{ width: 100%; border-collapse: collapse; page-break-inside: auto; }}
tr {{ page-break-inside: avoid; }}
th, td {{ text-align: left; vertical-align: top; padding: 2.5pt 4pt; border-bottom: 0.5pt solid #ccc; }}
th {{ font-weight: bold; background: #efefef; border-bottom: 0.75pt solid #999; }}
table.meta td:first-child {{ width: 32%; color: #444; }}
table.facts col.c1 {{ width: 22%; }} table.facts col.c2 {{ width: 15%; }}
table.facts col.c3 {{ width: 21%; }} table.facts col.c4 {{ width: 42%; }}
td.name {{ font-weight: bold; }}
td.evidence {{ color: #333; font-size: 8.5pt; }}
.where {{ color: #666; }}
.tag {{ font-weight: normal; font-size: 7.5pt; border: 0.5pt solid #777; padding: 0 2pt; }}
.none {{ color: #555; font-style: italic; margin: 2pt 0; }}
.note {{ font-size: 8.5pt; color: #333; }}
table.log td, table.log th {{ font-size: 8.5pt; }}
</style></head>
<body>
<h1>Aufnahmegespräch</h1>
<p class="sub">Strukturierte Anamnese aus dem aufgezeichneten Gespräch · ärztlich geprüft und freigegeben</p>
<table class="meta"><tbody>
<tr><td>Patientin / Patient</td><td>Zuordnung im Primärsystem (PVS / KIS); in dieser Anwendung nicht gespeichert</td></tr>
<tr><td>Gespräch</td><td>{_e(period)} ({minutes} Min.)</td></tr>
<tr><td>Gesprächssprache(n)</td><td>{_e(languages)}</td></tr>
<tr><td>Freigabe</td><td>{_e(_when(d.approved_at))} durch die behandelnde Ärztin / den behandelnden Arzt</td></tr>
<tr><td>Dokument-ID</td><td>{_e(d.document_id)}</td></tr>
</tbody></table>

<h2>1 Anwesende und Einwilligung in die Aufzeichnung</h2>
<table><thead><tr><th>Person</th><th>Rolle</th><th>Einwilligung</th><th>Zeitpunkt</th></tr></thead><tbody>{rows_people}</tbody></table>
<p class="note">Aufgezeichnet wurde nur, nachdem alle Anwesenden eingewilligt hatten (§ 201 StGB). Die Tonaufnahme wurde nach der Freigabe {('am ' + _e(_when(d.audio_deleted_at))) if d.audio_deleted_at else ''} gelöscht; aufbewahrt werden das Transkript und diese Dokumentation.</p>

<h2>2 Anamnese</h2>
<p class="note">Status: „ja“ = von der Patientin / dem Patienten angegeben, „verneint“ = ausdrücklich verneint, „früher“ = nicht mehr zutreffend. „Keine Angaben dokumentiert“ heißt nur, dass dazu nichts übernommen wurde, und ist keine Verneinung. Jede Angabe ist mit der wörtlichen Gesprächsstelle belegt (Zeile im Transkript, sprechende Person).</p>
{''.join(sections)}

<h2>3 Klärungsbedarf</h2>
{contra}

<h2>4 Änderungsprotokoll (§ 630f Abs. 1 BGB)</h2>
<p class="note">Änderungen der Ärztin / des Arztes am automatisch erstellten Entwurf vor der Freigabe, mit ursprünglichem Inhalt und Zeitpunkt.</p>
{change_log}

<h2>5 Hinweise zur Erstellung</h2>
<p class="note">Die Angaben wurden KI-gestützt aus dem Gesprächsmitschnitt strukturiert und vor der Freigabe ärztlich geprüft. Die Verarbeitung erfolgte ausschließlich lokal, ohne Cloud-Dienste (Clinic Dictation {_e(__version__)}; Spracherkennung: {_e(_model_name(d.transcription_model))}; Sprechertrennung: {_e(_model_name(d.diarization_model))}; Extraktion und Prüfung: {_e(_model_name(d.extraction_model))}). Das Dokument gibt wieder, was im Gespräch gesagt wurde; es enthält keine Diagnosen und keine Kodierungen. Eine maschinenlesbare Fassung liegt als HL7-FHIR-R4-Dokument mit derselben Dokument-ID vor. Für den Versand als eArztbrief über KIM ist das Dokument im Primärsystem mit dem elektronischen Heilberufsausweis qualifiziert elektronisch zu signieren.</p>
</body></html>"""
