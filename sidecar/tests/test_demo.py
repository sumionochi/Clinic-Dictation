from fastapi.testclient import TestClient

from clinic_sidecar.main import create_app
from tests.test_record import FakeLLM, valid
from tests.test_recording import AUTH, FakeDiarizer, FakeTranscriber


def make(tmp_path, dev: bool) -> TestClient:
    app = create_app(
        token="t",
        data_dir=tmp_path,
        transcriber=FakeTranscriber(),
        diarizer=FakeDiarizer(),
        llm_factory=FakeLLM,
        validator=valid,
        dev=dev,
    )
    return TestClient(app, headers=AUTH)


def test_demo_route_only_in_dev(tmp_path) -> None:
    assert make(tmp_path, dev=False).post("/dev/demo-session").status_code == 404


def test_demo_consultation_opens_ready_for_review(tmp_path) -> None:
    client = make(tmp_path, dev=True)
    sid = client.post("/dev/demo-session").json()["session_id"]

    session = client.get(f"/sessions/{sid}").json()
    assert [p["role"] for p in session["participants"]] == ["doctor", "patient", "companion"]
    assert all(p["decision"] == "granted" for p in session["participants"])
    assert len(client.get(f"/sessions/{sid}/consent-log").json()) == 3

    recording = client.get(f"/sessions/{sid}/recording").json()
    assert recording["status"] == "transcribed"
    assert {v["name"] for v in recording["speakers"]} == {"Doctor", "Patient", "Tochter"}

    record = client.get(f"/sessions/{sid}/record").json()
    assert record["status"] == "ready"
    assert record["lines"][0]["speaker"] == "Doctor"
    assert len(record["facts"]) == 9
    assert record["contradictions"][0]["name"] == "Metoprolol"
    # Every quote really is on the line it cites.
    lines = {ln["line"]: ln["text"] for ln in record["lines"]}
    for item in record["facts"]:
        for ev in item["fact"]["evidence"]:
            assert ev["quote"] in lines[ev["line"]]


def test_demo_consultation_can_be_approved(tmp_path) -> None:
    client = make(tmp_path, dev=True)
    sid = client.post("/dev/demo-session").json()["session_id"]
    assert client.post(f"/sessions/{sid}/record/approve").json()["status"] == "approved"
    assert client.get(f"/sessions/{sid}/record/fhir").json()["resourceType"] == "Bundle"


def test_each_demo_is_a_new_consultation(tmp_path) -> None:
    client = make(tmp_path, dev=True)
    first = client.post("/dev/demo-session").json()["session_id"]
    second = client.post("/dev/demo-session").json()["session_id"]
    assert first != second
