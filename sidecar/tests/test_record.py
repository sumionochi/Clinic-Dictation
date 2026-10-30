import time

import pytest
from fastapi.testclient import TestClient

from clinic_sidecar.agents.llm import CallLog, LLMError
from clinic_sidecar.agents.schema import Evidence, Fact, Facts, Verification
from clinic_sidecar.fhir.validator import Issue, ValidationReport
from clinic_sidecar.main import create_app
from tests.test_recording import AUTH, ONE_SECOND, FakeDiarizer, FakeTranscriber, consented_session, post_audio, wait_for

# FakeTranscriber says: line 1 "Haben Sie Allergien?" (S1), line 2 "No." (S2)


class FakeLLM:
    model = "fake-llm"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.log = CallLog()

    def structured(self, name, system, user, schema, max_tokens=4000):
        if self.fail:
            raise LLMError("Ollama is not running")
        if name in ("extract", "retry"):
            return Facts(
                facts=[
                    Fact(category="allergy", name="Allergien", assertion="absent",
                         evidence=[Evidence(line=1, quote="Allergien"), Evidence(line=2, quote="No")]),
                    # Rejected by the verifier, and contradicted by the next fact.
                    Fact(category="medication", name="Ramipril", assertion="present",
                         evidence=[Evidence(line=1, quote="Haben Sie")]),
                    Fact(category="medication", name="Ramipril", assertion="past",
                         evidence=[Evidence(line=2, quote="No")]),
                ]
            )
        claim = user.split("Claim to check:")[1]
        verdict = "unsupported" if "takes / reports: Ramipril" in claim else "supported"
        return Verification(reason="scripted", verdict=verdict)


def valid(_bundle: dict) -> ValidationReport:
    return ValidationReport(valid=True, errors=[], warnings=[])


def invalid(_bundle: dict) -> ValidationReport:
    return ValidationReport(valid=False, errors=[Issue("error", "Bundle.entry[0]", "broken")], warnings=[])


def make(tmp_path, llm_fail=False, validator=valid) -> TestClient:
    app = create_app(
        token="t",
        data_dir=tmp_path,
        transcriber=FakeTranscriber(),
        diarizer=FakeDiarizer(),
        llm_factory=lambda: FakeLLM(fail=llm_fail),
        validator=validator,
    )
    return TestClient(app, headers=AUTH)


def transcribed(client: TestClient) -> tuple[str, str]:
    sid = consented_session(client)
    rid = client.post(f"/sessions/{sid}/recordings", json={"language": "de"}).json()["id"]
    post_audio(client, sid, rid, ONE_SECOND)
    client.post(f"/sessions/{sid}/recordings/{rid}/stop")
    wait_for(client, sid, "transcribed")
    return sid, rid


def wait_record(client: TestClient, sid: str, *statuses: str) -> dict:
    for _ in range(200):
        body = client.get(f"/sessions/{sid}/record").json()
        if body and body["status"] in statuses:
            return body
        time.sleep(0.02)
    raise AssertionError(f"record still {body and body['status']}")


def ready_record(client: TestClient) -> tuple[str, str, dict]:
    sid, rid = transcribed(client)
    client.post(f"/sessions/{sid}/record")
    return sid, rid, wait_record(client, sid, "ready")


def test_no_record_before_transcript(tmp_path) -> None:
    client = make(tmp_path)
    sid = consented_session(client)
    assert client.get(f"/sessions/{sid}/record").json() is None
    assert client.post(f"/sessions/{sid}/record").status_code == 409


def test_extraction_keeps_supported_and_lists_rejected(tmp_path) -> None:
    client = make(tmp_path)
    _, _, body = ready_record(client)
    states = sorted((f["fact"]["name"], f["fact"]["assertion"], f["state"], f["origin"]) for f in body["facts"])
    assert states == [
        ("Allergien", "absent", "kept", "agents"),
        ("Ramipril", "past", "kept", "agents"),
        ("Ramipril", "present", "removed", "verifier_rejected"),
    ]
    rejected = next(f for f in body["facts"] if f["state"] == "removed")
    assert "unsupported" in rejected["reason"]
    # Found before verification, so the rejection does not hide it.
    assert [c["name"] for c in body["contradictions"]] == ["Ramipril"]
    assert [ln["speaker"] for ln in body["lines"]] == ["Speaker 1", "Speaker 2"]
    assert body["model"] == "fake-llm"


def test_llm_failure_is_shown_and_retryable(tmp_path) -> None:
    client = make(tmp_path, llm_fail=True)
    sid, _ = transcribed(client)
    client.post(f"/sessions/{sid}/record")
    body = wait_record(client, sid, "failed")
    assert "Ollama is not running" in body["error"]
    assert client.post(f"/sessions/{sid}/record").status_code == 200  # retry allowed


def test_doctor_edits_restores_and_removes(tmp_path) -> None:
    client = make(tmp_path)
    sid, _, body = ready_record(client)
    ids = {f["fact"]["name"]: f["id"] for f in body["facts"] if f["state"] == "removed" or f["fact"]["name"] == "Allergien"}
    url = f"/sessions/{sid}/record/facts"
    body = client.post(f"{url}/{ids['Ramipril']}", json={"action": "restore"}).json()
    body = client.post(f"{url}/{ids['Ramipril']}", json={"action": "edit", "details": "5 mg morgens"}).json()
    ramipril = next(f for f in body["facts"] if f["id"] == ids["Ramipril"])
    assert ramipril["state"] == "kept" and ramipril["origin"] == "doctor_edited"
    assert ramipril["fact"]["details"] == "5 mg morgens"
    assert ramipril["fact"]["evidence"]  # evidence stays attached
    body = client.post(f"{url}/{ids['Allergien']}", json={"action": "remove"}).json()
    assert next(f for f in body["facts"] if f["id"] == ids["Allergien"])["state"] == "removed"
    assert client.post(f"{url}/nope", json={"action": "remove"}).status_code == 404
    assert client.post(f"{url}/{ids['Allergien']}", json={"action": "edit", "assertion": "maybe"}).status_code == 422


def test_contradiction_follows_what_the_doctor_keeps(tmp_path) -> None:
    client = make(tmp_path)
    sid, _, body = ready_record(client)
    rejected = next(f["id"] for f in body["facts"] if f["state"] == "removed")
    url = f"/sessions/{sid}/record/facts/{rejected}"
    body = client.post(url, json={"action": "restore"}).json()
    assert [c["name"] for c in body["contradictions"]] == ["Ramipril"]
    body = client.post(url, json={"action": "remove"}).json()
    assert body["contradictions"] == []


def test_approve_validates_stores_and_deletes_audio(tmp_path) -> None:
    client = make(tmp_path)
    sid, rid, _ = ready_record(client)
    assert (tmp_path / "audio" / f"{rid}.pcm").exists()
    body = client.post(f"/sessions/{sid}/record/approve").json()
    assert body["status"] == "approved" and body["approved_at"]
    assert body["validation"]["errors"] == 0
    assert not (tmp_path / "audio" / f"{rid}.pcm").exists()
    rec = client.get(f"/sessions/{sid}/recording").json()
    assert rec["audio_deleted_at"] and len(rec["segments"]) == 2  # transcript kept
    bundle = client.get(f"/sessions/{sid}/record/fhir").json()
    composition = bundle["entry"][0]["resource"]
    assert composition["status"] == "final" and composition["attester"][0]["mode"] == "legal"
    # Approved records are frozen, and the audio cannot be re-transcribed.
    assert client.post(f"/sessions/{sid}/record/approve").status_code == 409
    assert client.post(f"/sessions/{sid}/record").status_code == 409
    assert client.post(f"/sessions/{sid}/recordings/{rid}/transcribe").status_code == 409


def test_invalid_document_blocks_approval_and_keeps_audio(tmp_path) -> None:
    client = make(tmp_path, validator=invalid)
    sid, rid, _ = ready_record(client)
    res = client.post(f"/sessions/{sid}/record/approve")
    assert res.status_code == 409 and "did not pass validation" in res.json()["detail"]
    assert client.get(f"/sessions/{sid}/record").json()["status"] == "ready"
    assert (tmp_path / "audio" / f"{rid}.pcm").exists()
    assert client.get(f"/sessions/{sid}/record/fhir").status_code == 409


def test_interrupted_extraction_becomes_failed_on_restart(tmp_path) -> None:
    client = make(tmp_path)
    sid, _ = transcribed(client)
    client.post(f"/sessions/{sid}/record")
    wait_record(client, sid, "ready")
    with client.app.state.consent.db.connect() as conn:
        conn.execute("UPDATE records SET status = 'extracting'")
    restarted = make(tmp_path)
    assert restarted.get(f"/sessions/{sid}/record").json()["status"] == "failed"


@pytest.mark.parametrize("action", ["remove", "restore"])
def test_changes_rejected_after_approval(tmp_path, action) -> None:
    client = make(tmp_path)
    sid, _, body = ready_record(client)
    client.post(f"/sessions/{sid}/record/approve")
    res = client.post(f"/sessions/{sid}/record/facts/{body['facts'][0]['id']}", json={"action": action})
    assert res.status_code == 409


def test_every_doctor_change_keeps_the_original_and_its_time(tmp_path) -> None:
    """§ 630f Abs. 1 BGB: the original content and the time of a correction stay visible."""
    client = make(tmp_path)
    sid, _, body = ready_record(client)
    fid = next(f["id"] for f in body["facts"] if f["fact"]["name"] == "Allergien")
    url = f"/sessions/{sid}/record/facts/{fid}"
    client.post(url, json={"action": "edit", "details": "keine bekannt"})
    client.post(url, json={"action": "remove"})
    body = client.post(url, json={"action": "restore"}).json()
    history = next(f for f in body["facts"] if f["id"] == fid)["history"]
    assert [h["action"] for h in history] == ["edit", "remove", "restore"]
    assert history[0]["before"]["details"] is None and history[0]["after"]["details"] == "keine bekannt"
    assert history[1]["before"]["state"] == "kept" and history[1]["after"]["state"] == "removed"
    assert all(h["at"] for h in history)
    # A no-op change is not logged.
    body = client.post(url, json={"action": "restore"}).json()
    assert len(next(f for f in body["facts"] if f["id"] == fid)["history"]) == 3


def approved(tmp_path, **kwargs) -> tuple[TestClient, str]:
    client = make(tmp_path, **kwargs)
    sid, _, body = ready_record(client)
    fid = next(f["id"] for f in body["facts"] if f["fact"]["name"] == "Allergien")
    client.post(f"/sessions/{sid}/record/facts/{fid}", json={"action": "edit", "details": "keine bekannt <b>"})
    client.post(f"/sessions/{sid}/record/approve")
    return client, sid


def test_readable_document_follows_the_rules(tmp_path) -> None:
    client, sid = approved(tmp_path)
    doc = client.get(f"/sessions/{sid}/record/document").json()
    html = doc["html"]
    bundle = client.get(f"/sessions/{sid}/record/fhir").json()
    assert doc["document_id"] == bundle["identifier"]["value"]  # both exports name the same document
    assert doc["filename"].startswith("aufnahmegespraech-")
    # § 630f Abs. 2: consents; § 201 StGB: everyone agreed before recording
    assert "Einwilligung in die Aufzeichnung" in html and "eingewilligt" in html
    # § 630f Abs. 1: the doctor's change, with the original
    assert "Änderungsprotokoll" in html and "geändert" in html and "keine bekannt" in html
    # Facts with their evidence; empty topics marked as not discussed, not as denied
    assert "Allergien" in html and "Z. 1" in html and "Keine Angaben dokumentiert." in html
    assert "verneint" in html
    assert "Im Gespräch nicht erhoben" not in html  # the app cannot know a topic never came up
    assert "<td>Arzt/Ärztin</td>" in html and "<td>Doctor</td>" not in html
    # AI transparency, no diagnoses, and where signing happens
    assert "KI-gestützt" in html and "keine Diagnosen" in html and "Heilberufsausweis" in html
    # Everything from the transcript or the doctor is escaped
    assert "keine bekannt &lt;b&gt;" in html and "keine bekannt <b>" not in html


def test_document_only_after_approval(tmp_path) -> None:
    client = make(tmp_path)
    sid, _, _ = ready_record(client)
    assert client.get(f"/sessions/{sid}/record/document").status_code == 409


def test_pdf_is_returned_only_if_pdfa_valid(tmp_path) -> None:
    from clinic_sidecar.document.pdfa import PdfaReport

    client, sid = approved(
        tmp_path,
    )
    service = client.app.state.records
    service.pdf_to_pdfa = lambda pdf, meta: pdf + b"%converted"
    service.pdfa_validator = lambda pdf: PdfaReport(True, "PDF/A-2B")
    res = client.post(f"/sessions/{sid}/record/document/pdf", content=b"%PDF-1.7 fake")
    assert res.status_code == 200 and res.content.endswith(b"%converted")
    assert res.headers["content-type"] == "application/pdf"

    service.pdfa_validator = lambda pdf: PdfaReport(False, "PDF/A-2B", ["6.2.11.4.1 font not embedded"])
    res = client.post(f"/sessions/{sid}/record/document/pdf", content=b"%PDF-1.7 fake")
    assert res.status_code == 409 and "font not embedded" in res.json()["detail"]
    assert client.post(f"/sessions/{sid}/record/document/pdf", content=b"not a pdf").status_code == 409
