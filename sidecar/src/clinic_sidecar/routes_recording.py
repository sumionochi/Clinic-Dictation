import asyncio

from fastapi import APIRouter, Request
from pydantic import BaseModel

from clinic_sidecar.consent import Conflict
from clinic_sidecar.recording import Language, RecordingService, RecordingView

router = APIRouter(prefix="/sessions/{session_id}")

# 1 s of 16 kHz 16-bit mono is 32 KB; anything far beyond that is not a chunk.
MAX_CHUNK_BYTES = 256 * 1024


class StartBody(BaseModel):
    language: Language | None = "de"


class MatchSpeakerBody(BaseModel):
    participant_id: str | None


def _service(request: Request) -> RecordingService:
    return request.app.state.recordings


@router.get("/recording")
def get_recording(session_id: str, request: Request) -> RecordingView | None:
    return _service(request).get(session_id)


@router.post("/recordings")
def start_recording(session_id: str, body: StartBody, request: Request) -> RecordingView:
    return _service(request).start(session_id, body.language)


@router.post("/recordings/{recording_id}/audio")
async def append_audio(session_id: str, recording_id: str, request: Request) -> dict[str, int]:
    chunk = await request.body()
    if len(chunk) > MAX_CHUNK_BYTES:
        raise Conflict("Audio chunk too large.")
    # fsync and SQLite block, so keep them off the event loop.
    samples = await asyncio.to_thread(_service(request).append_audio, session_id, recording_id, chunk)
    return {"samples": samples}


@router.post("/recordings/{recording_id}/stop")
def stop_recording(session_id: str, recording_id: str, request: Request) -> RecordingView:
    return _service(request).stop(session_id, recording_id)


@router.post("/recordings/{recording_id}/transcribe")
def transcribe(session_id: str, recording_id: str, request: Request) -> RecordingView:
    return _service(request).transcribe(session_id, recording_id)


@router.post("/recordings/{recording_id}/speakers/{speaker}")
def match_speaker(
    session_id: str, recording_id: str, speaker: str, body: MatchSpeakerBody, request: Request
) -> RecordingView:
    return _service(request).match_speaker(session_id, recording_id, speaker, body.participant_id)
