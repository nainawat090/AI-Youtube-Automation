"""
Phase 13: YouTube upload.

Uploads a project's finished, captioned video to the connected YouTube
channel (Phase 12) via the YouTube Data API v3's resumable upload protocol,
called directly over httpx — same "raw HTTP, no SDK" convention as every
provider in this project — then sets the custom thumbnail (Phase 9) on the
uploaded video.

Gated behind an explicit APPROVED status (see approve_project below), so a
finished video never reaches a real YouTube channel without a human having
looked at it first. The automated Phase 11 full-pipeline job stops at
READY_FOR_REVIEW and never calls this on its own — approval and upload are
always a deliberate, separate action.

The video is read fully into memory for the single PUT below rather than
implemented as true chunked resumable upload with per-chunk retry. That's
the right tradeoff for this project's short educational videos (well under
what fits comfortably in memory); a platform expecting long-form or very
large video files would want real chunking here instead.
"""

import os
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.models.base import JobStatus, LogLevel, ProjectStatus
from app.models.generation_log import GenerationLog
from app.models.project import Project
from app.models.youtube_upload import YouTubeUpload
from app.services.n8n_service import notify_n8n
from app.services.youtube_oauth_service import get_valid_access_token
from app.utils.logger import get_logger

log = get_logger(__name__)

UPLOAD_INIT_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
THUMBNAIL_URL = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set"
DEFAULT_CATEGORY_ID = "27"  # "Education" — this pipeline is built for short educational videos


class YouTubeUploadError(Exception):
    """Raised when any step of the upload or thumbnail-set request fails."""


def _get_project_or_404(project_id: uuid.UUID, db: Session) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


def approve_project(project_id: uuid.UUID, db: Session) -> Project:
    """
    The human-in-the-loop gate: marks a rendered, captioned video as
    reviewed and approved for upload. Nothing in this project uploads to
    a real YouTube channel without this being called first.
    """
    project = _get_project_or_404(project_id, db)
    if project.status != ProjectStatus.READY_FOR_REVIEW:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Project must be READY_FOR_REVIEW to approve (current status: {project.status.value})",
        )
    project.status = ProjectStatus.APPROVED
    db.add(
        GenerationLog(
            project_id=project_id, stage="approval", level=LogLevel.INFO, message="Project approved for upload"
        )
    )
    db.commit()
    db.refresh(project)
    log.info("project_approved", project_id=str(project_id))
    return project


async def _initiate_resumable_upload(
    access_token: str, project: Project, privacy_status: str, file_size: int
) -> str:
    tags = [t.strip() for t in (project.youtube_tags or "").split(",") if t.strip()]
    body = {
        "snippet": {
            "title": (project.youtube_title or project.title or "Untitled")[:100],
            "description": project.youtube_description or "",
            "tags": tags,
            "categoryId": DEFAULT_CATEGORY_ID,
        },
        "status": {"privacyStatus": privacy_status, "selfDeclaredMadeForKids": False},
    }
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": "video/mp4",
        "X-Upload-Content-Length": str(file_size),
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                UPLOAD_INIT_URL,
                params={"uploadType": "resumable", "part": "snippet,status"},
                headers=headers,
                json=body,
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise YouTubeUploadError(
            f"YouTube rejected the upload session: {exc.response.status_code} {exc.response.text}"
        ) from exc
    except httpx.HTTPError as exc:
        raise YouTubeUploadError(f"Network error starting the YouTube upload: {exc}") from exc

    upload_url = response.headers.get("Location")
    if not upload_url:
        raise YouTubeUploadError("YouTube did not return an upload session URL")
    return upload_url


async def _put_video_bytes(upload_url: str, video_path: str) -> dict:
    with open(video_path, "rb") as f:
        video_bytes = f.read()
    headers = {"Content-Type": "video/mp4", "Content-Length": str(len(video_bytes))}
    try:
        async with httpx.AsyncClient(timeout=600) as client:
            response = await client.put(upload_url, headers=headers, content=video_bytes)
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise YouTubeUploadError(
            f"YouTube rejected the video upload: {exc.response.status_code} {exc.response.text}"
        ) from exc
    except httpx.HTTPError as exc:
        raise YouTubeUploadError(f"Network error uploading the video to YouTube: {exc}") from exc
    return response.json()


async def _set_thumbnail(access_token: str, video_id: str, thumbnail_path: str) -> None:
    with open(thumbnail_path, "rb") as f:
        thumb_bytes = f.read()
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "image/jpeg"}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                THUMBNAIL_URL, params={"videoId": video_id}, headers=headers, content=thumb_bytes
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        # A failed thumbnail-set shouldn't undo an otherwise-successful
        # video upload — the video is already live either way. Log and
        # move on; the default YouTube-generated thumbnail is used instead.
        log.warning("youtube_thumbnail_set_failed", video_id=video_id, error=str(exc))


async def upload_project_to_youtube(
    project_id: uuid.UUID, db: Session, privacy_status_override: Optional[str] = None
) -> Project:
    """
    Uploads the project's rendered video to the connected YouTube channel,
    then sets its custom thumbnail. Requires the project to be APPROVED
    (see approve_project) — or already UPLOAD_STARTED/UPLOADING, for a
    retry after a previous attempt failed partway.
    """
    project = _get_project_or_404(project_id, db)

    if project.status not in (ProjectStatus.APPROVED, ProjectStatus.UPLOAD_STARTED, ProjectStatus.UPLOADING):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Project must be APPROVED before uploading to YouTube "
                f"(current status: {project.status.value}). Call POST .../approve first."
            ),
        )
    if not project.video_path or not os.path.exists(project.video_path):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Project has no rendered video file on disk")

    privacy_status = privacy_status_override or settings.youtube_default_privacy_status

    upload_row = YouTubeUpload(project_id=project_id, privacy_status=privacy_status, status=JobStatus.RUNNING)
    db.add(upload_row)
    project.status = ProjectStatus.UPLOAD_STARTED
    db.commit()
    db.refresh(upload_row)

    try:
        access_token = await get_valid_access_token(db)

        project.status = ProjectStatus.UPLOADING
        db.commit()

        file_size = os.path.getsize(project.video_path)
        upload_url = await _initiate_resumable_upload(access_token, project, privacy_status, file_size)
        video_resource = await _put_video_bytes(upload_url, project.video_path)
        video_id = video_resource["id"]
        video_url = f"https://www.youtube.com/watch?v={video_id}"

        if project.thumbnail_path and os.path.exists(project.thumbnail_path):
            await _set_thumbnail(access_token, video_id, project.thumbnail_path)
    except (YouTubeUploadError, HTTPException) as exc:
        error_message = exc.detail if isinstance(exc, HTTPException) else str(exc)
        upload_row.status = JobStatus.FAILED
        upload_row.attempt_count += 1
        upload_row.last_error = str(error_message)[:2000]
        project.status = ProjectStatus.APPROVED  # revert to a safe, retryable state
        db.add(
            GenerationLog(
                project_id=project_id,
                stage="youtube_upload",
                level=LogLevel.ERROR,
                message="YouTube upload failed",
                error_details=str(error_message)[:4000],
            )
        )
        db.commit()
        log.error("youtube_upload_failed", project_id=str(project_id), error=str(error_message))
        await notify_n8n("youtube_upload_failed", project, {"error": str(error_message)[:500]})
        status_code = exc.status_code if isinstance(exc, HTTPException) else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(status_code=status_code, detail=str(error_message)) from exc

    upload_row.status = JobStatus.SUCCEEDED
    upload_row.attempt_count += 1
    upload_row.youtube_video_id = video_id
    upload_row.youtube_url = video_url
    upload_row.uploaded_at = datetime.now(timezone.utc)

    project.status = ProjectStatus.UPLOADED
    project.youtube_video_id = video_id
    project.youtube_url = video_url

    db.add(
        GenerationLog(
            project_id=project_id,
            stage="youtube_upload",
            level=LogLevel.INFO,
            message=f"Uploaded to YouTube: {video_url}",
        )
    )
    db.commit()
    db.refresh(project)
    log.info("youtube_upload_succeeded", project_id=str(project_id), video_id=video_id)
    await notify_n8n("youtube_upload_succeeded", project)
    return project
