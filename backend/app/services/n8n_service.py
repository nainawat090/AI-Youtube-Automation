"""
Phase 14: outbound n8n notifications.

Sends a small JSON payload to the n8n Webhook-node URL configured as
N8N_OUTBOUND_WEBHOOK_URL, at the milestones an n8n workflow would actually
want to react to: a pipeline finishing (ready for human review), a pipeline
failing, and a YouTube upload succeeding or failing.

Same "raw HTTP, no SDK" convention as every other integration in this
project, and the same best-effort tradeoff already established by
youtube_upload_service._set_thumbnail: a notification failing (n8n down,
bad URL, network blip) must never fail or roll back the actual pipeline
work that already happened — it only gets logged.

If N8N_OUTBOUND_WEBHOOK_URL isn't set, notify_n8n() is a silent no-op, so
this integration has zero effect on anyone not using n8n.
"""

from typing import Any, Literal, Optional

import httpx

from app.config import settings
from app.models.project import Project
from app.utils.logger import get_logger

log = get_logger(__name__)

N8nEvent = Literal[
    "pipeline_ready_for_review",
    "pipeline_failed",
    "youtube_upload_succeeded",
    "youtube_upload_failed",
]


async def notify_n8n(event: N8nEvent, project: Project, extra: Optional[dict[str, Any]] = None) -> None:
    """
    Best-effort POST to the configured n8n webhook. Never raises — a
    notification failure is logged and swallowed, exactly like a failed
    thumbnail-set doesn't undo a successful YouTube upload.
    """
    if not settings.n8n_outbound_webhook_url:
        return

    payload = {
        "event": event,
        "project_id": str(project.id),
        "title": project.title,
        "status": project.status.value if project.status else None,
        "youtube_video_id": project.youtube_video_id,
        "youtube_url": project.youtube_url,
        "last_error": project.last_error,
    }
    if extra:
        payload.update(extra)

    headers = {}
    if settings.n8n_outbound_webhook_secret:
        headers["X-Webhook-Secret"] = settings.n8n_outbound_webhook_secret

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(settings.n8n_outbound_webhook_url, json=payload, headers=headers)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        # `event` is structlog's own reserved kwarg name for the log
        # message itself, so the n8n event type is passed as `n8n_event`.
        log.warning("n8n_notify_failed", n8n_event=event, project_id=str(project.id), error=str(exc))
