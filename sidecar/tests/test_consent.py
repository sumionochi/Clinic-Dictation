import sqlite3

import pytest
from fastapi.testclient import TestClient

from clinic_sidecar.main import create_app

AUTH = {"Authorization": "Bearer t"}


@pytest.fixture
def client(tmp_path) -> TestClient:
    return TestClient(create_app(token="t", data_dir=tmp_path), headers=AUTH)


def new_session(client: TestClient) -> str:
    res = client.post("/sessions")
    assert res.status_code == 200
    return res.json()["id"]


def add(client: TestClient, sid: str, role: str, label: str | None = None) -> str:
    res = client.post(f"/sessions/{sid}/participants", json={"role": role, "label": label})
    assert res.status_code == 200, res.text
    return res.json()["participants"][-1]["id"]


def decide(client: TestClient, sid: str, pid: str, decision: str) -> dict:
    res = client.post(f"/sessions/{sid}/participants/{pid}/consent", json={"decision": decision})
    assert res.status_code == 200, res.text
    return res.json()


def test_empty_session_is_locked(client: TestClient) -> None:
    body = client.get(f"/sessions/{new_session(client)}").json()
    assert body["recording_allowed"] is False
    assert body["blockers"] == ["Add the doctor.", "Add the patient."]


def test_everyone_agrees_unlocks_recording(client: TestClient) -> None:
    sid = new_session(client)
    doctor, patient = add(client, sid, "doctor"), add(client, sid, "patient")
    decide(client, sid, doctor, "granted")
    body = decide(client, sid, patient, "granted")
    assert body["recording_allowed"] is True
    assert body["blockers"] == []


def test_doctor_must_consent_too(client: TestClient) -> None:
    sid = new_session(client)
    add(client, sid, "doctor")
    body = decide(client, sid, add(client, sid, "patient"), "granted")
    assert body["recording_allowed"] is False
    assert body["blockers"] == ["Doctor has not answered yet."]


def test_companion_added_later_locks_again(client: TestClient) -> None:
    sid = new_session(client)
    decide(client, sid, add(client, sid, "doctor"), "granted")
    decide(client, sid, add(client, sid, "patient"), "granted")
    add(client, sid, "companion", "Daughter")
    body = client.get(f"/sessions/{sid}").json()
    assert body["recording_allowed"] is False
    assert body["blockers"] == ["Daughter has not answered yet."]


@pytest.mark.parametrize(
    ("decision", "blocker"),
    [("declined", "Patient declined. Document manually."), ("unable", "Patient cannot consent. Document manually.")],
)
def test_decline_or_unable_blocks(client: TestClient, decision: str, blocker: str) -> None:
    sid = new_session(client)
    decide(client, sid, add(client, sid, "doctor"), "granted")
    body = decide(client, sid, add(client, sid, "patient"), decision)
    assert body["recording_allowed"] is False
    assert body["blockers"] == [blocker]


def test_change_of_mind_is_logged_not_overwritten(client: TestClient) -> None:
    sid = new_session(client)
    decide(client, sid, add(client, sid, "doctor"), "granted")
    patient = add(client, sid, "patient")
    decide(client, sid, patient, "declined")
    assert decide(client, sid, patient, "granted")["recording_allowed"] is True
    log = client.get(f"/sessions/{sid}/consent-log").json()
    assert [(e["display_name"], e["decision"]) for e in log] == [
        ("Doctor", "granted"),
        ("Patient", "declined"),
        ("Patient", "granted"),
    ]


def test_duplicate_roles_get_numbered(client: TestClient) -> None:
    sid = new_session(client)
    add(client, sid, "companion")
    add(client, sid, "companion")
    names = [p["display_name"] for p in client.get(f"/sessions/{sid}").json()["participants"]]
    assert names == ["Companion 1", "Companion 2"]


def test_remove_only_before_answering(client: TestClient) -> None:
    sid = new_session(client)
    mistake = add(client, sid, "student")
    assert client.delete(f"/sessions/{sid}/participants/{mistake}").status_code == 200
    answered = add(client, sid, "patient")
    decide(client, sid, answered, "granted")
    assert client.delete(f"/sessions/{sid}/participants/{answered}").status_code == 409


def test_manual_mode_locks_and_freezes(client: TestClient) -> None:
    sid = new_session(client)
    decide(client, sid, add(client, sid, "doctor"), "granted")
    patient = add(client, sid, "patient")
    decide(client, sid, patient, "declined")
    body = client.post(f"/sessions/{sid}/manual").json()
    assert body["mode"] == "manual"
    assert body["recording_allowed"] is False
    assert client.post(f"/sessions/{sid}/participants/{patient}/consent", json={"decision": "granted"}).status_code == 409
    assert client.post(f"/sessions/{sid}/participants", json={"role": "student"}).status_code == 409


def test_validation_and_not_found(client: TestClient) -> None:
    sid = new_session(client)
    assert client.post(f"/sessions/{sid}/participants", json={"role": "boss"}).status_code == 422
    assert client.post(f"/sessions/{sid}/participants", json={"role": "patient", "label": "x" * 41}).status_code == 422
    assert client.get("/sessions/nope").status_code == 404
    other = new_session(client)
    pid = add(client, other, "patient")
    res = client.post(f"/sessions/{sid}/participants/{pid}/consent", json={"decision": "granted"})
    assert res.status_code == 404


def test_data_survives_restart(tmp_path) -> None:
    first = TestClient(create_app(token="t", data_dir=tmp_path), headers=AUTH)
    sid = new_session(first)
    decide(first, sid, add(first, sid, "patient"), "granted")
    second = TestClient(create_app(token="t", data_dir=tmp_path), headers=AUTH)
    assert second.get(f"/sessions/{sid}").json()["participants"][0]["decision"] == "granted"


def test_consent_log_cannot_be_edited(tmp_path) -> None:
    client = TestClient(create_app(token="t", data_dir=tmp_path), headers=AUTH)
    sid = new_session(client)
    decide(client, sid, add(client, sid, "patient"), "declined")
    conn = sqlite3.connect(tmp_path / "clinic.sqlite")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE consent_events SET decision = 'granted'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM consent_events")
