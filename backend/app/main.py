from __future__ import annotations

import logging
import threading
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from .config import Settings, get_settings
from .worker import ScreeningWorker

settings = get_settings()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logging.getLogger("agent_chain").setLevel(logging.INFO)


def _queue_loop(stop_event: threading.Event) -> None:
    logger = logging.getLogger("worker.queue")
    worker = ScreeningWorker()
    while not stop_event.is_set():
        try:
            result = worker.run_once()
            delay = 0.3 if result.get("processed") else 2.0
            if result.get("processed"):
                logger.info(
                    "queue_tick processed task_id=%s type=%s",
                    result.get("task_id"),
                    result.get("task_type"),
                )
        except Exception:
            logger.exception("queue_tick failed")
            delay = 2.0
        stop_event.wait(delay)


@asynccontextmanager
async def lifespan(app: FastAPI):
    stop_event = threading.Event()
    thread: threading.Thread | None = None
    if settings.auto_process_queue:
        thread = threading.Thread(target=_queue_loop, args=(stop_event,), daemon=True)
        thread.start()
    yield
    stop_event.set()
    if thread:
        thread.join(timeout=2)


app = FastAPI(title="Resume Screening Worker", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Internal-Token"],
)


class ProcessJobRequest(BaseModel):
    job_id: str


def require_internal_token(
    x_internal_token: Optional[str] = Header(default=None),
    config: Settings = Depends(get_settings),
) -> None:
    if x_internal_token != config.internal_api_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal token")


def _process_job(job_id: str) -> dict[str, object]:
    return ScreeningWorker().process_job_until_done(job_id)


def _ensure_local_dev(request: Request, config: Settings) -> None:
    """Loopback-only. Mock and openai local one-click both use this unauthenticated path."""
    del config
    client_host = request.client.host if request.client else ""
    if client_host not in {"127.0.0.1", "::1", "localhost"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Local dev only")


def _require_job_owner(job_id: str, authorization: Optional[str]) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing user token")
    worker = ScreeningWorker()
    try:
        user = worker.client.auth.get_user(authorization.removeprefix("Bearer ").strip()).user
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user token") from exc
    job = (
        worker.client.table("screening_jobs")
        .select("id,created_by")
        .eq("id", job_id)
        .single()
        .execute()
        .data
    )
    if not job or str(job.get("created_by")) != str(user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")


@app.get("/")
def root() -> HTMLResponse:
    """Worker is API-only; send humans to the local frontend."""
    frontend = "http://127.0.0.1:4174/index.html"
    return HTMLResponse(
        f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"/>
<meta http-equiv="refresh" content="0;url={frontend}"/>
<title>觅才 Worker</title></head>
<body style="font-family:system-ui;padding:40px;line-height:1.6">
  <h1>这里是解析 Worker（API）</h1>
  <p>页面请打开：<a href="{frontend}">{frontend}</a></p>
  <p>健康检查：<a href="/health">/health</a></p>
</body></html>""",
        status_code=200,
    )


@app.get("/health")
def health() -> dict[str, str]:
    c = settings.construction_llm()
    k = settings.checker_llm()
    construction = (
        "OpenAIConstructionAgent"
        if settings.agent_mode == "openai" and c.get("api_key") and c.get("base_url")
        else "MockConstructionAgent"
    )
    checker = (
        "OpenAICheckerAgent"
        if settings.agent_mode == "openai" and k.get("api_key") and k.get("base_url")
        else "MockCheckerAgent"
    )
    return {
        "status": "ok",
        "agent_mode": settings.agent_mode,
        "construction": construction,
        "checker": checker,
    }


@app.post("/internal/tasks/run-once", dependencies=[Depends(require_internal_token)])
def run_once() -> dict[str, object]:
    """Run one queued task. Invoke from a scheduler or background job platform."""
    return ScreeningWorker().run_once()


@app.post("/internal/jobs/process", dependencies=[Depends(require_internal_token)])
def process_job(body: ProcessJobRequest) -> dict[str, object]:
    """Process every queued task for a screening job until it finishes."""
    return _process_job(body.job_id)


@app.post("/dev/jobs/process")
def process_job_dev(body: ProcessJobRequest, request: Request, config: Settings = Depends(get_settings)) -> dict[str, object]:
    """Localhost-only endpoint so the browser can one-click parse without exposing tokens."""
    _ensure_local_dev(request, config)
    return _process_job(body.job_id)


@app.post("/jobs/process")
def process_job_for_user(body: ProcessJobRequest, authorization: Optional[str] = Header(default=None)) -> dict[str, object]:
    """Process one user-owned job. Intended for authenticated serverless deployments."""
    _require_job_owner(body.job_id, authorization)
    return _process_job(body.job_id)
