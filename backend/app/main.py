from __future__ import annotations

from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

from .config import Settings, get_settings
from .worker import ScreeningWorker

settings = get_settings()
app = FastAPI(title="Resume Screening Worker", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Internal-Token"],
)


def require_internal_token(
    x_internal_token: Optional[str] = Header(default=None),
    config: Settings = Depends(get_settings),
) -> None:
    if x_internal_token != config.internal_api_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal token")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/internal/tasks/run-once", dependencies=[Depends(require_internal_token)])
def run_once() -> dict[str, object]:
    """Run one queued task. Invoke from a scheduler or background job platform."""
    return ScreeningWorker().run_once()
