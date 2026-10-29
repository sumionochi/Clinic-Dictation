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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from clinic_sidecar import __version__

TOKEN_ENV = "SIDECAR_TOKEN"
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
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    watcher = asyncio.create_task(_exit_when_orphaned(os.getppid()))
    yield
    watcher.cancel()


def create_app(token: str | None = None) -> FastAPI:
    token = token if token is not None else os.environ.get(TOKEN_ENV, "")
    if not token:
        raise RuntimeError(f"{TOKEN_ENV} must be set; the sidecar never runs without auth")

    app = FastAPI(
        title="Clinic Dictation sidecar",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        lifespan=_lifespan,
    )
    started_at = time.monotonic()

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
