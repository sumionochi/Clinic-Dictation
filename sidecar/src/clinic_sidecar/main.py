"""FastAPI app for the Clinic Dictation sidecar.

Electron starts this with uvicorn on 127.0.0.1 and a random port, and passes a
per-launch token in SIDECAR_TOKEN. Every request must carry that token, so
other programs on the machine (or a web page probing localhost) cannot read
anything from it.
"""

import asyncio
import os
import secrets
import signal
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from clinic_sidecar import __version__
from clinic_sidecar.agents.llm import LLM, OllamaLLM
from clinic_sidecar.consent import Conflict, ConsentService, NotFound
from clinic_sidecar.db import Database
from clinic_sidecar.diarizer import Diarizer, SortformerDiarizer
from clinic_sidecar.fhir.validator import ValidationReport, validate
from clinic_sidecar.record import RecordService
from clinic_sidecar.recording import RecordingService
from clinic_sidecar.routes_consent import router as consent_router
from clinic_sidecar.routes_record import router as record_router
from clinic_sidecar.routes_recording import router as recording_router
from clinic_sidecar.transcriber import Transcriber, TranscriptionWorker, WhisperTranscriber

TOKEN_ENV = "SIDECAR_TOKEN"
DATA_DIR_ENV = "CLINIC_DATA_DIR"
PARENT_CHECK_S = 2.0


async def _exit_when_orphaned(parent_pid: int) -> None:
    """If Electron dies without stopping us (crash, force quit), our parent pid
    changes. Shut down instead of lingering in the background with audio data."""
    while True:
        await asyncio.sleep(PARENT_CHECK_S)
        if os.getppid() != parent_pid:
            os.kill(os.getpid(), signal.SIGTERM)
            return


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    watcher = asyncio.create_task(_exit_when_orphaned(os.getppid()))
    yield
    watcher.cancel()
    app.state.worker.shutdown()


def create_app(
    token: str | None = None,
    data_dir: Path | None = None,
    transcriber: Transcriber | None = None,
    diarizer: Diarizer | None = None,
    llm_factory: Callable[[], LLM] | None = None,
    validator: Callable[[dict], ValidationReport] | None = None,
) -> FastAPI:
    token = token if token is not None else os.environ.get(TOKEN_ENV, "")
    if not token:
        raise RuntimeError(f"{TOKEN_ENV} must be set; the sidecar never runs without auth")
    if data_dir is None:
        if not os.environ.get(DATA_DIR_ENV):
            raise RuntimeError(f"{DATA_DIR_ENV} must be set to the folder for local data")
        data_dir = Path(os.environ[DATA_DIR_ENV])

    app = FastAPI(
        title="Clinic Dictation sidecar",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        lifespan=_lifespan,
    )
    started_at = time.monotonic()
    db = Database(data_dir)
    app.state.consent = ConsentService(db)
    app.state.worker = TranscriptionWorker()
    app.state.recordings = RecordingService(
        db,
        data_dir,
        app.state.consent,
        transcriber or WhisperTranscriber(),
        diarizer or SortformerDiarizer(),
        app.state.worker,
    )
    app.state.records = RecordService(
        db, app.state.consent, app.state.recordings, llm_factory or OllamaLLM, validator or validate
    )
    app.include_router(consent_router)
    app.include_router(recording_router)
    app.include_router(record_router)

    @app.exception_handler(NotFound)
    async def not_found(_request: Request, exc: NotFound) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(Conflict)
    async def conflict(_request: Request, exc: Conflict) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.middleware("http")
    async def require_token(request: Request, call_next):
        auth = request.headers.get("authorization", "")
        scheme, _, given = auth.partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(given, token):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)

    @app.get("/health")
    async def health() -> dict[str, str | float]:
        return {
            "status": "ok",
            "service": "clinic-sidecar",
            "version": __version__,
            "uptime_s": round(time.monotonic() - started_at, 1),
        }

    return app
