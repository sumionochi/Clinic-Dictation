import asyncio
from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from clinic_sidecar.agents.schema import Assertion
from clinic_sidecar.consent import Conflict
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


@router.get("/document")
def document(session_id: str, request: Request) -> dict:
    """The approved record as a printable German document (HTML)."""
    return _service(request).document_html(session_id)


# The printed document is at most a few MB; anything far beyond that is not one.
MAX_PDF_BYTES = 30 * 1024 * 1024


@router.post("/document/pdf")
async def document_pdf(session_id: str, request: Request) -> Response:
    """Takes the PDF the app printed from /document; returns it as validated PDF/A-2b."""
    body = await request.body()
    if not body.startswith(b"%PDF") or len(body) > MAX_PDF_BYTES:
        raise Conflict("Expected a PDF.")
    converted = await asyncio.to_thread(_service(request).document_pdfa, session_id, body)
    return Response(content=converted, media_type="application/pdf")


@router.get("/fhir")
def fhir_bundle(session_id: str, request: Request) -> dict:
    return _service(request).exported_bundle(session_id)
