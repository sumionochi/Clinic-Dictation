from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from clinic_sidecar.agents.schema import Assertion
from clinic_sidecar.record import RecordService, RecordView

router = APIRouter(prefix="/sessions/{session_id}/record")


class FactChange(BaseModel):
    action: Literal["remove", "restore", "edit"]
    name: str | None = Field(default=None, min_length=1, max_length=60)
    details: str | None = Field(default=None, max_length=120)
    assertion: Assertion | None = None


def _service(request: Request) -> RecordService:
    return request.app.state.records


@router.get("")
def get_record(session_id: str, request: Request) -> RecordView | None:
    return _service(request).get(session_id)


@router.post("")
def start_extraction(session_id: str, request: Request) -> RecordView:
    return _service(request).start(session_id)


@router.post("/facts/{fact_id}")
def change_fact(session_id: str, fact_id: str, body: FactChange, request: Request) -> RecordView:
    return _service(request).update_fact(
        session_id, fact_id, body.action, name=body.name, details=body.details, assertion=body.assertion
    )


@router.post("/approve")
def approve(session_id: str, request: Request) -> RecordView:
    return _service(request).approve(session_id)


@router.get("/fhir")
def fhir_bundle(session_id: str, request: Request) -> dict:
    return _service(request).exported_bundle(session_id)
