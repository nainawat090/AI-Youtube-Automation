"""
FastAPI application entrypoint.

Phase 2: app boots, structured logging is configured, CORS is set up, the
database is reachable, and the Projects API (create/list/get/delete/status)
is live and backed by real PostgreSQL tables via SQLAlchemy + Alembic.
Phase 3 adds prompt -> script (+raw scenes) via the configured AI provider.
Phase 4 adds the Scenes API: reviewing, editing and finalizing that scene
breakdown before Phase 5 (images) and Phase 6 (voice) consume it. The rest
of the pipeline (visuals/voice/render/upload) is added in Phases 5-13 per
DEVELOPMENT_ORDER.md.
"""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api import jobs as jobs_api
from app.api import projects as projects_api
from app.api import scenes as scenes_api
from app.api import webhooks as webhooks_api
from app.api import youtube as youtube_api
from app.config import settings
from app.database import engine
from app.utils.logger import configure_logging, get_logger

configure_logging()
log = get_logger(__name__)

app = FastAPI(
    title="AI YouTube Automation Platform",
    description="Prompt-to-published-video pipeline: script, scenes, visuals, voice, captions, render, upload.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(projects_api.router)
app.include_router(scenes_api.router)
app.include_router(jobs_api.router)
app.include_router(youtube_api.router)
app.include_router(webhooks_api.router)

# Phase 10: serves the generated video/thumbnail/captions files the
# frontend needs to actually display — Project.video_path etc. have always
# been local filesystem paths (e.g. "./output/<id>/final_captioned.mp4"),
# never reachable over HTTP until now. Read-only, and — same tradeoff as
# everywhere else in this single-operator self-hosted tool (see
# YouTubeCredential's own docstring) — not behind auth. Directory names
# intentionally mirror settings.output_path / settings.local_storage_path
# so the frontend can turn a stored path into a URL with a plain string
# replace (see frontend/src/utils/media.ts).
os.makedirs(settings.output_path, exist_ok=True)
os.makedirs(settings.local_storage_path, exist_ok=True)
app.mount("/static/output", StaticFiles(directory=settings.output_path), name="output")
app.mount("/static/assets", StaticFiles(directory=settings.local_storage_path), name="assets")


@app.on_event("startup")
async def on_startup() -> None:
    log.info(
        "app_startup",
        app_env=settings.app_env,
        ai_provider=settings.ai_provider,
        tts_provider=settings.tts_provider,
    )


@app.get("/api/health", tags=["health"])
async def health_check() -> dict:
    """Liveness/readiness probe, including a real DB connectivity check."""
    db_status = "ok"
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - health check reports any failure, doesn't distinguish
        db_status = f"error: {exc}"
        log.error("health_check_db_failed", error=str(exc))

    return {
        "status": "ok" if db_status == "ok" else "degraded",
        "app_env": settings.app_env,
        "version": app.version,
        "database": db_status,
    }


@app.get("/", tags=["health"])
async def root() -> dict:
    return {"message": "AI YouTube Automation Platform API", "docs": "/docs"}
