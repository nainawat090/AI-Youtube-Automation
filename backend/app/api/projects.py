"""
Projects API (section 19). Started in Phase 2 as create/list/get/delete a
project and poll its status; every pipeline-triggering endpoint (script,
images, audio, render, captions, thumbnail, the full background pipeline,
approve, and YouTube upload) was added in its corresponding later phase
(3-13) as that phase's service layer became ready to back it.
"""

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.base import LogLevel
from app.models.generation_log import GenerationLog
from app.models.job import Job
from app.models.project import Project
from app.models.youtube_upload import YouTubeUpload
from app.schemas.job import JobResponse
from app.schemas.project import (
    ProjectCreate,
    ProjectListResponse,
    ProjectResponse,
    ProjectStatusResponse,
)
from app.schemas.youtube import YouTubeUploadRequest, YouTubeUploadResponse
from app.services.ai_provider import AIProviderError
from app.services.caption_service import generate_project_captions
from app.services.pipeline_service import enqueue_full_pipeline
from app.services.render_service import render_project_video
from app.services.thumbnail_service import generate_project_thumbnail
from app.services.script_service import generate_script
from app.services.tts_service import generate_project_audio
from app.services.visual_service import generate_project_images
from app.services.youtube_upload_service import approve_project, upload_project_to_youtube
from app.utils.logger import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)) -> Project:
    """
    Create a new project row from the user's prompt. Status starts at CREATED.
    Does NOT start generation — that happens via POST /{id}/generate once the
    AI pipeline exists (Phase 3+).
    """
    project = Project(
        user_prompt=payload.user_prompt,
        language=payload.language,
        target_audience=payload.target_audience,
        duration_seconds=payload.duration_seconds,
    )
    db.add(project)
    db.flush()  # populate project.id before using it in the log row

    db.add(
        GenerationLog(
            project_id=project.id,
            stage="project_creation",
            level=LogLevel.INFO,
            message="Project created",
        )
    )
    db.commit()
    db.refresh(project)

    log.info("project_created", project_id=str(project.id))
    return project


@router.get("", response_model=ProjectListResponse)
def list_projects(
    skip: int = 0,
    limit: int = 25,
    db: Session = Depends(get_db),
) -> ProjectListResponse:
    """List projects, most recent first."""
    items = db.scalars(
        select(Project).order_by(Project.created_at.desc()).offset(skip).limit(limit)
    ).all()
    total = db.scalar(select(func.count()).select_from(Project)) or 0
    return ProjectListResponse(items=list(items), total=total)


@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(project_id: uuid.UUID, db: Session = Depends(get_db)) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


@router.get("/{project_id}/status", response_model=ProjectStatusResponse)
def get_project_status(project_id: uuid.UUID, db: Session = Depends(get_db)) -> Project:
    """Lightweight endpoint for frontend polling (section 18)."""
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


@router.post("/{project_id}/generate-script", response_model=ProjectResponse)
async def generate_project_script(project_id: uuid.UUID, db: Session = Depends(get_db)) -> Project:
    """
    Phase 3: send the project's prompt to the configured LLM (AI_PROVIDER),
    validate the structured JSON it returns, and persist the script + scenes.
    Runs synchronously and blocks until the LLM responds. For the whole
    pipeline at once without blocking on each stage, see Phase 11's
    POST .../pipeline instead.
    """
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    try:
        project = await generate_script(project_id, db)
    except AIProviderError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        log.error("generate_script_endpoint_failed", project_id=str(project_id), error=str(exc))
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    return project


@router.post("/{project_id}/generate-images", response_model=ProjectResponse)
async def generate_project_images_endpoint(project_id: uuid.UUID, db: Session = Depends(get_db)) -> Project:
    """
    Phase 5: generate an image for every scene via the configured
    VISUAL_PROVIDER (openai_images or stock_pexels). Requires scenes to
    already be finalized (POST .../scenes/finalize). Safe to call again
    after a partial failure — scenes that already succeeded are skipped.
    """
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    return await generate_project_images(project_id, db)


@router.post("/{project_id}/generate-audio", response_model=ProjectResponse)
async def generate_project_audio_endpoint(project_id: uuid.UUID, db: Session = Depends(get_db)) -> Project:
    """
    Phase 6: generate narration audio for every scene via the configured
    TTS_PROVIDER (edge_tts, elevenlabs, or openai_tts). Requires scene
    images to already exist (POST .../generate-images). Safe to call again
    after a partial failure — scenes that already have audio are skipped.
    """
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    return await generate_project_audio(project_id, db)


@router.post("/{project_id}/render", response_model=ProjectResponse)
async def render_project_endpoint(project_id: uuid.UUID, db: Session = Depends(get_db)) -> Project:
    """
    Phase 7: render every scene's clip (image + audio + on-screen text) via
    FFmpeg and stitch them into the final project video. Requires every
    scene to have narration audio (POST .../generate-audio). Safe to call
    again after a partial failure — scenes already rendered are skipped.
    """
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    return await render_project_video(project_id, db)


@router.post("/{project_id}/captions", response_model=ProjectResponse)
async def generate_project_captions_endpoint(project_id: uuid.UUID, db: Session = Depends(get_db)) -> Project:
    """
    Phase 8: generate an SRT captions file from every scene's narration,
    timed to the already-rendered final video, and burn it into that video.
    Requires POST .../render to have already produced a final video. Safe
    to call again any time (e.g. after re-rendering following a scene edit).
    """
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    return await generate_project_captions(project_id, db)


@router.post("/{project_id}/thumbnail", response_model=ProjectResponse)
async def generate_project_thumbnail_endpoint(project_id: uuid.UUID, db: Session = Depends(get_db)) -> Project:
    """
    Phase 9: generate a 1280x720 YouTube thumbnail — an AI-generated
    background plus the video's title burned in. Requires the project's
    script to exist (POST .../generate-script). Independent of the render/
    captions pipeline status, so it can be called any time after that. Safe
    to call again — always regenerates both the background and the composite.
    """
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    return await generate_project_thumbnail(project_id, db)


@router.post("/{project_id}/approve", response_model=ProjectResponse)
def approve_project_endpoint(project_id: uuid.UUID, db: Session = Depends(get_db)) -> Project:
    """
    Phase 13: mark a finished (READY_FOR_REVIEW) video as reviewed and
    approved for upload. This is the human-in-the-loop gate — nothing in
    this project uploads to a real YouTube channel without this being
    called first, including the Phase 11 background pipeline, which stops
    at READY_FOR_REVIEW on purpose.
    """
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    return approve_project(project_id, db)


@router.post("/{project_id}/youtube/upload", response_model=ProjectResponse)
async def upload_project_to_youtube_endpoint(
    project_id: uuid.UUID, payload: YouTubeUploadRequest = YouTubeUploadRequest(), db: Session = Depends(get_db)
) -> Project:
    """
    Phase 13: upload the project's rendered video to the connected YouTube
    channel (POST /api/youtube/oauth/login must be connected first) and
    set its custom thumbnail. Requires the project to be APPROVED
    (POST .../approve). Safe to call again after a failed attempt — the
    project reverts to APPROVED on any failure so this can simply be
    retried. Privacy defaults to YOUTUBE_DEFAULT_PRIVACY_STATUS unless
    overridden in the request body.
    """
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    return await upload_project_to_youtube(project_id, db, payload.privacy_status)


@router.get("/{project_id}/youtube/uploads", response_model=List[YouTubeUploadResponse])
def list_project_youtube_uploads(project_id: uuid.UUID, db: Session = Depends(get_db)) -> List[YouTubeUpload]:
    """List a project's YouTube upload attempts, most recent first."""
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    return list(
        db.scalars(
            select(YouTubeUpload).where(YouTubeUpload.project_id == project_id).order_by(YouTubeUpload.created_at.desc())
        )
    )


@router.post("/{project_id}/pipeline", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
def run_pipeline_endpoint(project_id: uuid.UUID, db: Session = Depends(get_db)) -> Job:
    """
    Phase 11: run the ENTIRE pipeline (script -> scenes -> images -> audio
    -> render -> captions -> thumbnail) in the background via Celery,
    instead of calling each Phase 3-9 endpoint one at a time and waiting on
    each. Returns immediately (202) with a Job to poll — GET
    /api/jobs/{job_id} or GET /{project_id}/jobs — rather than blocking the
    request for however long the full run takes (easily several minutes).

    This is purely an additional way to trigger the same work: every
    individual endpoint from Phases 3-9 still works exactly as before, and
    is still the right choice for running just one stage and getting the
    result immediately.
    """
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    job = enqueue_full_pipeline(project_id, db)
    log.info("pipeline_job_enqueued", project_id=str(project_id), job_id=str(job.id), task_id=job.celery_task_id)
    return job


@router.get("/{project_id}/jobs", response_model=List[JobResponse])
def list_project_jobs(project_id: uuid.UUID, db: Session = Depends(get_db)) -> List[Job]:
    """List a project's background jobs, most recent first."""
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    return list(
        db.scalars(select(Job).where(Job.project_id == project_id).order_by(Job.created_at.desc()))
    )


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: uuid.UUID, db: Session = Depends(get_db)) -> None:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    db.delete(project)  # cascades to scenes/assets/jobs/uploads/logs
    db.commit()
    log.info("project_deleted", project_id=str(project_id))
