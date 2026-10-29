import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from clinic_sidecar.main import create_app
from clinic_sidecar.transcriber import Segment, Word

AUTH = {"Authorization": "Bearer t"}
ONE_SECOND = np.zeros(16_000, dtype="<i2").tobytes()


class FakeTranscriber:
    model_name = "fake-whisper"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[int, str | None]] = []

    def transcribe(self, audio: np.ndarray, language: str | None) -> list[Segment]:
        self.calls.append((audio.size, language))
        if self.fail:
            raise RuntimeError("model exploded")
        return [
            Segment(0.0, 1.2, "Haben Sie Allergien?", [Word("Haben", 0.0, 0.3, 0.9)]),
            Segment(1.5, 2.0, "Nein.", []),
        ]


def make_client(tmp_path, transcriber=None) -> tuple[TestClient, FakeTranscriber]:
    transcriber = transcriber or FakeTranscriber()
    app = create_app(token="t", data_dir=tmp_path, transcriber=transcriber)
    return TestClient(app, headers=AUTH), transcriber


def consented_session(client: TestClient) -> str:
    sid = client.post("/sessions").json()["id"]
    for role in ("doctor", "patient"):
        pid = client.post(f"/sessions/{sid}/participants", json={"role": role}).json()["participants"][-1]["id"]
        client.post(f"/sessions/{sid}/participants/{pid}/consent", json={"decision": "granted"})
    return sid


def wait_for(client: TestClient, sid: str, *statuses: str) -> dict:
    for _ in range(100):
        body = client.get(f"/sessions/{sid}/recording").json()
        if body["status"] in statuses:
            return body
        time.sleep(0.02)
    raise AssertionError(f"still {body['status']}")


def post_audio(client: TestClient, sid: str, rid: str, data: bytes):
    return client.post(
        f"/sessions/{sid}/recordings/{rid}/audio",
        content=data,
        headers={"Content-Type": "application/octet-stream"},
    )


def test_cannot_record_without_consent(tmp_path) -> None:
    client, _ = make_client(tmp_path)
    sid = client.post("/sessions").json()["id"]
    res = client.post(f"/sessions/{sid}/recordings", json={"language": "de"})
    assert res.status_code == 409
    assert "Recording is locked" in res.json()["detail"]


def test_record_stop_transcribe(tmp_path) -> None:
    client, fake = make_client(tmp_path)
    sid = consented_session(client)
    rid = client.post(f"/sessions/{sid}/recordings", json={"language": "de"}).json()["id"]
    assert post_audio(client, sid, rid, ONE_SECOND).json() == {"samples": 16_000}
    assert post_audio(client, sid, rid, ONE_SECOND).json() == {"samples": 32_000}

    client.post(f"/sessions/{sid}/recordings/{rid}/stop")
    body = wait_for(client, sid, "transcribed")
    assert body["duration_s"] == 2.0
    assert body["model"] == "fake-whisper"
    assert [s["text"] for s in body["segments"]] == ["Haben Sie Allergien?", "Nein."]
    assert body["segments"][0]["words"][0]["word"] == "Haben"
    assert fake.calls == [(32_000, "de")]


def test_audio_is_on_disk_after_each_chunk(tmp_path) -> None:
    client, _ = make_client(tmp_path)
    sid = consented_session(client)
    rid = client.post(f"/sessions/{sid}/recordings", json={"language": None}).json()["id"]
    post_audio(client, sid, rid, ONE_SECOND)
    assert (tmp_path / "audio" / f"{rid}.pcm").stat().st_size == len(ONE_SECOND)


def test_new_person_in_room_stops_audio(tmp_path) -> None:
    client, _ = make_client(tmp_path)
    sid = consented_session(client)
    rid = client.post(f"/sessions/{sid}/recordings", json={"language": "de"}).json()["id"]
    assert post_audio(client, sid, rid, ONE_SECOND).status_code == 200
    client.post(f"/sessions/{sid}/participants", json={"role": "companion"})
    res = post_audio(client, sid, rid, ONE_SECOND)
    assert res.status_code == 409
    assert (tmp_path / "audio" / f"{rid}.pcm").stat().st_size == len(ONE_SECOND)


def test_rejects_bad_chunks_and_second_recording(tmp_path) -> None:
    client, _ = make_client(tmp_path)
    sid = consented_session(client)
    rid = client.post(f"/sessions/{sid}/recordings", json={"language": "de"}).json()["id"]
    assert post_audio(client, sid, rid, b"\x00").status_code == 409
    assert post_audio(client, sid, rid, b"\x00" * (300 * 1024)).status_code == 409
    assert client.post(f"/sessions/{sid}/recordings", json={"language": "de"}).status_code == 409
    assert client.post(f"/sessions/{sid}/recordings", json={"language": "fr"}).status_code == 422


def test_failed_transcription_can_be_retried(tmp_path) -> None:
    fake = FakeTranscriber(fail=True)
    client, _ = make_client(tmp_path, fake)
    sid = consented_session(client)
    rid = client.post(f"/sessions/{sid}/recordings", json={"language": "de"}).json()["id"]
    post_audio(client, sid, rid, ONE_SECOND)
    client.post(f"/sessions/{sid}/recordings/{rid}/stop")
    failed = wait_for(client, sid, "failed")
    assert "model exploded" in failed["error"]

    fake.fail = False
    client.post(f"/sessions/{sid}/recordings/{rid}/transcribe")
    assert len(wait_for(client, sid, "transcribed")["segments"]) == 2


def test_empty_recording_fails_clearly(tmp_path) -> None:
    client, _ = make_client(tmp_path)
    sid = consented_session(client)
    rid = client.post(f"/sessions/{sid}/recordings", json={"language": "de"}).json()["id"]
    client.post(f"/sessions/{sid}/recordings/{rid}/stop")
    assert "No audio was recorded" in wait_for(client, sid, "failed")["error"]


def test_crash_mid_recording_keeps_audio(tmp_path) -> None:
    client, _ = make_client(tmp_path)
    sid = consented_session(client)
    rid = client.post(f"/sessions/{sid}/recordings", json={"language": "de"}).json()["id"]
    post_audio(client, sid, rid, ONE_SECOND)
    # Simulate audio written to disk but the app dying before the database update.
    with open(tmp_path / "audio" / f"{rid}.pcm", "ab") as f:
        f.write(ONE_SECOND)

    restarted, fake = make_client(tmp_path)
    body = restarted.get(f"/sessions/{sid}/recording").json()
    assert body["status"] == "interrupted"
    assert body["duration_s"] == 2.0
    restarted.post(f"/sessions/{sid}/recordings/{rid}/transcribe")
    wait_for(restarted, sid, "transcribed")
    assert fake.calls == [(32_000, "de")]


def test_no_recording_yet_returns_null(tmp_path) -> None:
    client, _ = make_client(tmp_path)
    sid = consented_session(client)
    assert client.get(f"/sessions/{sid}/recording").json() is None
    assert client.get("/sessions/nope/recording").status_code == 404


@pytest.mark.parametrize("status", ["transcribing", "transcribed"])
def test_cannot_retranscribe_while_busy_or_done(tmp_path, status: str) -> None:
    client, _ = make_client(tmp_path)
    sid = consented_session(client)
    rid = client.post(f"/sessions/{sid}/recordings", json={"language": "de"}).json()["id"]
    post_audio(client, sid, rid, ONE_SECOND)
    client.post(f"/sessions/{sid}/recordings/{rid}/stop")
    wait_for(client, sid, "transcribed")
    if status == "transcribing":
        app = client.app
        with app.state.consent.db.connect() as conn:
            conn.execute("UPDATE recordings SET status = 'transcribing'")
    assert client.post(f"/sessions/{sid}/recordings/{rid}/transcribe").status_code == 409
