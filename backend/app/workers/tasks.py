"""
Phase 11: Celery tasks.

Each task is a thin synchronous wrapper around the existing async
service-layer functions from Phases 3-9. A Celery worker process is plain
sync (no request lifecycle, no already-running event loop), so each task
opens its own short-lived asyncio event loop (asyncio.run) and its own DB
session (SessionLocal — see database.py's documented Celery usage) rather
than reusing anything from the FastAPI process, which runs separately.
"""

import asyncio
import uuid

from fastapi import HTTPException

from app.database import SessionLocal
from app.models.base import JobStatus, utcnow
from app.models.job import Job
from app.models.project import Project
from app.services.caption_service import generate_project_captions
from app.services.n8n_service import notify_n8n
from app.services.render_service import render_project_video
from app.services.scene_service import finalize_scenes
from app.services.script_service import generate_script
from app.services.thumbnail_service import generate_project_thumbnail
from app.services.tts_service import generate_project_audio
from app.services.visual_service import generate_project_images
from app.utils.logger import get_logger
from app.workers.celery_app import celery_app

log = get_logger(__name__)


def _stage_error_message(stage: str, exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        detail = exc.detail
        if isinstance(detail, dict):
            detail = detail.get("message", detail)
        return f"[{stage}] {exc.status_code}: {detail}"
    return f"[{stage}] {exc}"


@celery_app.task(name="run_full_pipeline", bind=True, max_retries=0)
def run_full_pipeline_task(self, project_id: str, job_id: str) -> None:
    """
    Runs the entire prompt-to-video pipeline for one project: generate the
    script, finalize scenes, generate images, generate audio, render,
    burn in captions, generate the thumbnail — the same steps as calling
    each Phase 3-9 endpoint in order.

    Stops at whichever stage fails first. Every stage function already
    reverts project.status to a safe, retryable state on its own failure
    (that's how the synchronous per-stage endpoints have worked since
    Phase 5), so this task doesn't duplicate that logic — it just records
    which stage failed onto the Job row and stops the chain. Re-running
    this task (POST .../pipeline again), or simply calling the individual
    endpoint for the failed stage, picks up where it left off, since every
    stage function already skips work that's already done.
    """
    db = SessionLocal()
    try:
        job = db.get(Job, uuid.UUID(job_id))
        if job is None:
            log.error("full_pipeline_task_missing_job", project_id=project_id, job_id=job_id)
            return

        job.status = JobStatus.RUNNING
        job.celery_task_id = self.request.id
        job.attempt_count += 1
        db.commit()

        pid = uuid.UUID(project_id)
        stage = "generate_script"
        try:
            asyncio.run(generate_script(pid, db))

            stage = "finalize_scenes"
            finalize_scenes(pid, db)

            stage = "generate_images"
            asyncio.run(generate_project_images(pid, db))

            stage = "generate_audio"
            asyncio.run(generate_project_audio(pid, db))

            stage = "render"
            asyncio.run(render_project_video(pid, db))

            stage = "captions"
            asyncio.run(generate_project_captions(pid, db))

            stage = "thumbnail"
            asyncio.run(generate_project_thumbnail(pid, db))
        except Exception as exc:  # noqa: BLE001 - any stage failure ends the run; message says which stage
            job.status = JobStatus.FAILED
            job.last_error = _stage_error_message(stage, exc)[:2000]
            job.last_attempt_at = utcnow()
            db.commit()
            log.error(
                "full_pipeline_task_failed",
                project_id=project_id,
                job_id=job_id,
                stage=stage,
                error=str(exc),
            )
            failed_project = db.get(Project, pid)
            if failed_project is not None:
                asyncio.run(notify_n8n("pipeline_failed", failed_project, {"stage": stage, "job_id": job_id}))
            return

        job.status = JobStatus.SUCCEEDED
        job.last_error = None
        job.last_attempt_at = utcnow()
        db.commit()
        log.info("full_pipeline_task_succeeded", project_id=project_id, job_id=job_id)
        finished_project = db.get(Project, pid)
        if finished_project is not None:
            asyncio.run(notify_n8n("pipeline_ready_for_review", finished_project, {"job_id": job_id}))
    finally:
        db.close()
