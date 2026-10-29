from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from clinic_sidecar.consent import ConsentEvent, ConsentService, Decision, Role, SessionView

router = APIRouter(prefix="/sessions")


class AddParticipantBody(BaseModel):
    role: Role
    # A role is enough. The label is for telling two companions apart, e.g. "Daughter";
    # the UI asks for no names.
    label: str | None = Field(default=None, max_length=40)


class DecisionBody(BaseModel):
    decision: Decision


def _service(request: Request) -> ConsentService:
    return request.app.state.consent


@router.post("")
def create_session(request: Request) -> SessionView:
    return _service(request).create_session()


@router.get("/{session_id}")
def get_session(session_id: str, request: Request) -> SessionView:
    return _service(request).get_session(session_id)


@router.post("/{session_id}/participants")
def add_participant(session_id: str, body: AddParticipantBody, request: Request) -> SessionView:
    return _service(request).add_participant(session_id, body.role, body.label)


@router.delete("/{session_id}/participants/{participant_id}")
def remove_participant(session_id: str, participant_id: str, request: Request) -> SessionView:
    return _service(request).remove_participant(session_id, participant_id)


@router.post("/{session_id}/participants/{participant_id}/consent")
def record_decision(
    session_id: str, participant_id: str, body: DecisionBody, request: Request
) -> SessionView:
    return _service(request).record_decision(session_id, participant_id, body.decision)


@router.post("/{session_id}/manual")
def switch_to_manual(session_id: str, request: Request) -> SessionView:
    return _service(request).switch_to_manual(session_id)


@router.get("/{session_id}/consent-log")
def consent_log(session_id: str, request: Request) -> list[ConsentEvent]:
    return _service(request).consent_log(session_id)
