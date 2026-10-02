"""
Phase 14: n8n integration, inbound side.

One endpoint: POST /api/webhooks/n8n/trigger. It exists so an n8n workflow
(a schedule, a new row in a Google Sheet/Airtable, a form submission, a
Telegram message, whatever n8n is watching) can start a brand-new video
with a single HTTP call, instead of you opening Swagger and calling
POST /api/projects then POST /{id}/pipeline yourself.

It does exactly those two things — create the project, enqueue the full
background pipeline (Phase 11) — and nothing more. It deliberately does
NOT accept anything that would approve or upload a video: Phase 13's
human-in-the-loop approval gate stays intact for every video, whether it
was started from Swagger or from n8n. The pipeline this kicks off stops at
READY_FOR_REVIEW, same as always; see app/services/n8n_service.py for how
n8n is notified once it gets there.

Auth: a shared secret, N8N_INBOUND_WEBHOOK_SECRET, checked against the
X-Webhook-Secret header. This is a single-operator, self-hosted tool with
no broader auth/session layer (see YouTubeCredential's own docstring for
the same tradeoff), so a shared secret is the proportionate amount of
protection here — not full user auth. If the secret isn't configured, the
endpoint still works (useful for purely local testing) but logs a warning
on every call so it's never silently exposed by accident.
"""

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.generation_log import GenerationLog
from app.models.base import LogLevel
from app.models.project import Project
from app.schemas.job import JobResponse
from app.schemas.webhook import N8nTriggerRequest
from app.services.pipeline_service import enqueue_full_pipeline
from app.utils.logger import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


def _check_inbound_secret(x_webhook_secret: str | None) -> None:
    configured = settings.n8n_inbound_webhook_secret
    if not configured:
        log.warning("n8n_trigger_called_without_configured_secret")
        return
    if x_webhook_secret != configured:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing X-Webhook-Secret")


@router.post("/n8n/trigger", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
def n8n_trigger_pipeline(
    payload: N8nTriggerRequest,
    db: Session = Depends(get_db),
    x_webhook_secret: str | None = Header(default=None),
) -> object:
    """
    Creates a new project from `user_prompt` and immediately enqueues the
    full background pipeline for it (same as POST /{id}/pipeline). Returns
    202 with a Job to poll — GET /api/jobs/{job_id} — the video itself
    takes several minutes to finish, same as every other way of starting one.
    """
    _check_inbound_secret(x_webhook_secret)

    project = Project(
        user_prompt=payload.user_prompt,
        language=payload.language,
        target_audience=payload.target_audience,
        duration_seconds=payload.duration_seconds,
    )
    db.add(project)
    db.flush()  # populate project.id before using it below

    db.add(
        GenerationLog(
            project_id=project.id,
            stage="project_creation",
            level=LogLevel.INFO,
            message="Project created via n8n webhook trigger",
        )
    )
    db.commit()
    db.refresh(project)

    job = enqueue_full_pipeline(project.id, db)
    log.info("n8n_trigger_pipeline_enqueued", project_id=str(project.id), job_id=str(job.id))
    return job
